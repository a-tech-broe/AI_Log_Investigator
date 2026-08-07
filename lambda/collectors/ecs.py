"""ECS evidence: service health, deployments, and stopped-task forensics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3

from collectors import collector
from utils.config import BOTO_CONFIG, Config
from utils.logger import get_logger
from utils.parser import Alert

log = get_logger(__name__)

# Stop reasons that point at a specific class of failure the LLM should weigh.
_FAILURE_HINTS = {
    "OutOfMemoryError": "container_oom",
    "OOMKilled": "container_oom",
    "CannotPullContainerError": "image_pull_failure",
    "ResourceInitializationError": "resource_init_failure",
    "Task failed ELB health checks": "failed_health_checks",
    "Essential container in task exited": "essential_container_exit",
    "Scaling activity initiated": "scaling_event",
}


def _client(cfg: Config):
    return boto3.client("ecs", region_name=cfg.aws_region, config=BOTO_CONFIG)


def _minutes_ago(moment: datetime | None) -> float | None:
    if not moment:
        return None
    if not moment.tzinfo:
        moment = moment.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - moment
    return round(delta.total_seconds() / 60, 1)


def _classify(reason: str) -> str | None:
    for needle, label in _FAILURE_HINTS.items():
        if needle.lower() in reason.lower():
            return label
    return None


def _resolve_service_arn(client, cluster: str, service: str) -> str:
    """Match the alert's service label against real ECS service names.

    Grafana labels rarely match the ECS service name exactly (`payment-api` vs
    `payment-api-prod`), so fall back to a prefix/substring match.
    """
    paginator = client.get_paginator("list_services")
    names: list[str] = []
    for page in paginator.paginate(cluster=cluster):
        names.extend(page.get("serviceArns", []))

    short = {arn.rsplit("/", 1)[-1]: arn for arn in names}
    if service in short:
        return short[service]

    candidates = [name for name in short if name.startswith(service) or service in name]
    if candidates:
        best = min(candidates, key=len)
        log.info(
            "resolved alert service to ECS service by fuzzy match",
            extra={"alert_service": service, "ecs_service": best},
        )
        return short[best]

    raise LookupError(f"no ECS service in cluster '{cluster}' matches '{service}'")


def _deployments(service: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for dep in service.get("deployments", []):
        out.append(
            {
                "id": dep.get("id"),
                "status": dep.get("status"),
                "rollout_state": dep.get("rolloutState"),
                "rollout_state_reason": dep.get("rolloutStateReason"),
                "task_definition": (dep.get("taskDefinition") or "").rsplit("/", 1)[-1],
                "desired_count": dep.get("desiredCount"),
                "running_count": dep.get("runningCount"),
                "pending_count": dep.get("pendingCount"),
                "failed_tasks": dep.get("failedTasks"),
                "created_minutes_ago": _minutes_ago(dep.get("createdAt")),
                "updated_minutes_ago": _minutes_ago(dep.get("updatedAt")),
            }
        )
    return out


def _stopped_tasks(client, cluster: str, service_name: str, limit: int) -> list[dict[str, Any]]:
    listed = client.list_tasks(
        cluster=cluster,
        serviceName=service_name,
        desiredStatus="STOPPED",
        maxResults=min(limit, 100),
    )
    arns = listed.get("taskArns", [])
    if not arns:
        return []

    described = client.describe_tasks(cluster=cluster, tasks=arns[:limit])
    tasks = []
    for task in described.get("tasks", []):
        reason = task.get("stoppedReason", "") or ""
        containers = [
            {
                "name": c.get("name"),
                "exit_code": c.get("exitCode"),
                "reason": c.get("reason"),
            }
            for c in task.get("containers", [])
            if c.get("exitCode") not in (0, None) or c.get("reason")
        ]
        tasks.append(
            {
                "task_id": (task.get("taskArn") or "").rsplit("/", 1)[-1],
                "task_definition": (task.get("taskDefinitionArn") or "").rsplit("/", 1)[-1],
                "stopped_reason": reason,
                "stop_code": task.get("stopCode"),
                "failure_class": _classify(reason),
                "stopped_minutes_ago": _minutes_ago(task.get("stoppedAt")),
                "containers": containers,
            }
        )
    tasks.sort(key=lambda t: t["stopped_minutes_ago"] if t["stopped_minutes_ago"] is not None else 1e9)
    return tasks


@collector("ecs")
def collect(cfg: Config, alert: Alert) -> dict[str, Any]:
    """Gather service counts, deployment state, and recent task failures."""
    cluster = alert.cluster or cfg.ecs_cluster
    if not cluster:
        return {
            "available": False,
            "error": "no ECS cluster in alert labels or ECS_CLUSTER env var",
        }

    client = _client(cfg)
    service_arn = _resolve_service_arn(client, cluster, alert.service)
    service_name = service_arn.rsplit("/", 1)[-1]

    described = client.describe_services(cluster=cluster, services=[service_arn])
    services = described.get("services", [])
    if not services:
        raise LookupError(f"describe_services returned nothing for '{service_name}'")
    service = services[0]

    deployments = _deployments(service)
    primary = next((d for d in deployments if d["status"] == "PRIMARY"), None)
    stopped = _stopped_tasks(client, cluster, service_name, cfg.ecs_stopped_task_limit)

    desired = service.get("desiredCount", 0)
    running = service.get("runningCount", 0)
    pending = service.get("pendingCount", 0)

    return {
        "cluster": cluster,
        "service": service_name,
        "status": service.get("status"),
        "launch_type": service.get("launchType"),
        "desired_count": desired,
        "running_count": running,
        "pending_count": pending,
        "capacity_shortfall": max(desired - running, 0),
        "is_healthy": desired == running and pending == 0,
        "task_definition": (service.get("taskDefinition") or "").rsplit("/", 1)[-1],
        "deployments": deployments,
        "deployment_in_progress": any(d.get("rollout_state") == "IN_PROGRESS" for d in deployments),
        "recent_deployment_minutes_ago": primary["created_minutes_ago"] if primary else None,
        "stopped_task_count": len(stopped),
        "stopped_tasks": stopped,
        "failure_classes": sorted({t["failure_class"] for t in stopped if t["failure_class"]}),
        "service_events": [
            {
                "message": event.get("message"),
                "minutes_ago": _minutes_ago(event.get("createdAt")),
            }
            for event in service.get("events", [])[:10]
        ],
    }
