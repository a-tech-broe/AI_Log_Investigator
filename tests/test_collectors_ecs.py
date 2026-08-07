from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from collectors import ecs


class FakeEcsClient:
    """Minimal ECS stub covering the shapes the collector reads."""

    def __init__(self, *, services=None, service_arns=None, stopped=None, running_tasks=None):
        self.service_arns = service_arns or ["arn:aws:ecs:us-east-1:1:service/prod-ecs/payment-api"]
        self.services = services or []
        self.stopped = stopped or []
        self.running_tasks = running_tasks or []

    def get_paginator(self, _name):
        arns = self.service_arns

        class Paginator:
            def paginate(self, **_kwargs):
                return [{"serviceArns": arns}]

        return Paginator()

    def describe_services(self, **_kwargs):
        return {"services": self.services}

    def list_tasks(self, **kwargs):
        if kwargs.get("desiredStatus") == "STOPPED":
            return {"taskArns": [f"arn:aws:ecs:us-east-1:1:task/prod-ecs/{t['id']}" for t in self.stopped]}
        return {"taskArns": self.running_tasks}

    def describe_tasks(self, **_kwargs):
        return {
            "tasks": [
                {
                    "taskArn": f"arn:aws:ecs:us-east-1:1:task/prod-ecs/{t['id']}",
                    "taskDefinitionArn": "arn:aws:ecs:us-east-1:1:task-definition/payment-api:42",
                    "stoppedReason": t["reason"],
                    "stopCode": t.get("stop_code", "TaskFailedToStart"),
                    "stoppedAt": t.get("stopped_at", datetime.now(UTC) - timedelta(minutes=4)),
                    "containers": t.get("containers", []),
                }
                for t in self.stopped
            ]
        }


def _service(**overrides):
    now = datetime.now(UTC)
    base = {
        "serviceArn": "arn:aws:ecs:us-east-1:1:service/prod-ecs/payment-api",
        "serviceName": "payment-api",
        "status": "ACTIVE",
        "launchType": "FARGATE",
        "desiredCount": 6,
        "runningCount": 4,
        "pendingCount": 2,
        "taskDefinition": "arn:aws:ecs:us-east-1:1:task-definition/payment-api:42",
        "deployments": [
            {
                "id": "ecs-svc/123",
                "status": "PRIMARY",
                "rolloutState": "IN_PROGRESS",
                "rolloutStateReason": "ECS deployment in progress.",
                "taskDefinition": "arn:aws:ecs:us-east-1:1:task-definition/payment-api:42",
                "desiredCount": 6,
                "runningCount": 4,
                "pendingCount": 2,
                "failedTasks": 3,
                "createdAt": now - timedelta(minutes=8),
                "updatedAt": now - timedelta(minutes=1),
            }
        ],
        "events": [
            {"message": "service payment-api has started 1 tasks", "createdAt": now - timedelta(minutes=2)}
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture
def patch_client(monkeypatch):
    def _patch(client):
        monkeypatch.setattr(ecs, "_client", lambda _cfg: client)
        return client

    return _patch


def test_collects_service_health_and_deployment(cfg, alert, patch_client):
    patch_client(
        FakeEcsClient(
            services=[_service()],
            stopped=[
                {
                    "id": "task-a",
                    "reason": "Essential container in task exited",
                    "containers": [{"name": "app", "exitCode": 137, "reason": None}],
                }
            ],
        )
    )

    result = ecs.collect(cfg, alert)

    assert result["available"] is True
    assert result["desired_count"] == 6
    assert result["running_count"] == 4
    assert result["capacity_shortfall"] == 2
    assert result["is_healthy"] is False
    assert result["deployment_in_progress"] is True
    assert result["recent_deployment_minutes_ago"] == pytest.approx(8.0, abs=0.2)
    assert result["task_definition"] == "payment-api:42"
    assert result["stopped_task_count"] == 1
    assert result["failure_classes"] == ["essential_container_exit"]
    assert result["service_events"][0]["message"].startswith("service payment-api")


def test_oom_kill_is_classified(cfg, alert, patch_client):
    patch_client(
        FakeEcsClient(
            services=[_service()],
            stopped=[{"id": "t1", "reason": "OutOfMemoryError: Container killed due to memory usage"}],
        )
    )

    result = ecs.collect(cfg, alert)

    assert result["failure_classes"] == ["container_oom"]
    assert result["stopped_tasks"][0]["failure_class"] == "container_oom"


def test_healthy_service_reports_healthy(cfg, alert, patch_client):
    patch_client(FakeEcsClient(services=[_service(desiredCount=6, runningCount=6, pendingCount=0)]))

    result = ecs.collect(cfg, alert)

    assert result["is_healthy"] is True
    assert result["capacity_shortfall"] == 0
    assert result["stopped_task_count"] == 0


def test_fuzzy_matches_suffixed_ecs_service_name(cfg, alert, patch_client):
    patch_client(
        FakeEcsClient(
            service_arns=[
                "arn:aws:ecs:us-east-1:1:service/prod-ecs/checkout-api-prod",
                "arn:aws:ecs:us-east-1:1:service/prod-ecs/payment-api-prod",
            ],
            services=[_service(serviceName="payment-api-prod")],
        )
    )

    result = ecs.collect(cfg, alert)

    assert result["service"] == "payment-api-prod"


def test_unknown_service_degrades_instead_of_raising(cfg, alert, patch_client):
    patch_client(FakeEcsClient(service_arns=["arn:aws:ecs:us-east-1:1:service/prod-ecs/unrelated"]))

    result = ecs.collect(cfg, alert)

    assert result["available"] is False
    assert "LookupError" in result["error"]


def test_missing_cluster_is_reported_not_raised(cfg, alert, monkeypatch):
    monkeypatch.setattr(alert, "cluster", "")
    monkeypatch.setenv("ECS_CLUSTER", "")
    from utils import config as config_module

    config_module.reset_caches()

    result = ecs.collect(config_module.load_config(), alert)

    assert result["available"] is False
    assert "cluster" in result["error"]
