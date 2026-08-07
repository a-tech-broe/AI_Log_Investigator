"""CloudWatch evidence: utilization, ALB error rates, and latency."""

from __future__ import annotations

from typing import Any

import boto3

from collectors import collector
from utils.config import BOTO_CONFIG, Config
from utils.logger import get_logger
from utils.parser import Alert

log = get_logger(__name__)


def _client(cfg: Config):
    return boto3.client("cloudwatch", region_name=cfg.aws_region, config=BOTO_CONFIG)


def _query(
    query_id: str,
    namespace: str,
    metric: str,
    dimensions: dict[str, str],
    stat: str,
    period: int,
) -> dict[str, Any]:
    return {
        "Id": query_id,
        "MetricStat": {
            "Metric": {
                "Namespace": namespace,
                "MetricName": metric,
                "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
            },
            "Period": period,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def _build_queries(cfg: Config, cluster: str, service: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return (metric data queries, query_id -> human label)."""
    period = cfg.metric_period_seconds
    ecs_dims = {"ClusterName": cluster, "ServiceName": service}

    queries = [
        _query("cpu", "AWS/ECS", "CPUUtilization", ecs_dims, "Average", period),
        _query("mem", "AWS/ECS", "MemoryUtilization", ecs_dims, "Average", period),
        _query("cpu_max", "AWS/ECS", "CPUUtilization", ecs_dims, "Maximum", period),
        _query("mem_max", "AWS/ECS", "MemoryUtilization", ecs_dims, "Maximum", period),
    ]
    labels = {
        "cpu": "cpu_utilization_avg_pct",
        "mem": "memory_utilization_avg_pct",
        "cpu_max": "cpu_utilization_max_pct",
        "mem_max": "memory_utilization_max_pct",
    }

    # Container Insights is opt-in per cluster; absent metrics simply return no
    # datapoints, so querying unconditionally is safe.
    insights_dims = {"ClusterName": cluster, "ServiceName": service}
    queries.append(
        _query("running", "ECS/ContainerInsights", "RunningTaskCount", insights_dims, "Average", period)
    )
    labels["running"] = "running_task_count"

    if cfg.target_group_arn_suffix and cfg.alb_arn_suffix:
        tg_dims = {
            "TargetGroup": cfg.target_group_arn_suffix,
            "LoadBalancer": cfg.alb_arn_suffix,
        }
        queries += [
            _query("alb_5xx", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", tg_dims, "Sum", period),
            _query("alb_4xx", "AWS/ApplicationELB", "HTTPCode_Target_4XX_Count", tg_dims, "Sum", period),
            _query("alb_req", "AWS/ApplicationELB", "RequestCount", tg_dims, "Sum", period),
            _query("alb_rt", "AWS/ApplicationELB", "TargetResponseTime", tg_dims, "Average", period),
            _query("alb_healthy", "AWS/ApplicationELB", "HealthyHostCount", tg_dims, "Average", period),
            _query("alb_unhealthy", "AWS/ApplicationELB", "UnHealthyHostCount", tg_dims, "Average", period),
        ]
        labels.update(
            {
                "alb_5xx": "alb_5xx_count",
                "alb_4xx": "alb_4xx_count",
                "alb_req": "alb_request_count",
                "alb_rt": "alb_response_time_seconds",
                "alb_healthy": "alb_healthy_hosts",
                "alb_unhealthy": "alb_unhealthy_hosts",
            }
        )

    return queries, labels


def _summarize(values: list[float], stat: str) -> dict[str, float] | None:
    if not values:
        return None
    # get_metric_data returns newest-first when ScanBy=TimestampDescending.
    latest = values[0]
    summary = {
        "latest": round(latest, 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "avg": round(sum(values) / len(values), 3),
        "datapoints": len(values),
    }
    if stat == "Sum":
        summary["total"] = round(sum(values), 3)
    return summary


@collector("cloudwatch")
def collect(cfg: Config, alert: Alert) -> dict[str, Any]:
    """Pull the metric window around the alert and summarize each series."""
    cluster = alert.cluster or cfg.ecs_cluster
    if not cluster:
        return {"available": False, "error": "no ECS cluster available for metric dimensions"}

    start, end = alert.window(cfg.lookback_minutes)
    queries, labels = _build_queries(cfg, cluster, alert.service)

    client = _client(cfg)
    response = client.get_metric_data(
        MetricDataQueries=queries,
        StartTime=start,
        EndTime=end,
        ScanBy="TimestampDescending",
        MaxDatapoints=1000,
    )

    stats_by_id = {q["Id"]: q["MetricStat"]["Stat"] for q in queries}
    metrics: dict[str, Any] = {}
    for result in response.get("MetricDataResults", []):
        label = labels.get(result["Id"], result["Id"])
        summary = _summarize([float(v) for v in result.get("Values", [])], stats_by_id[result["Id"]])
        if summary:
            metrics[label] = summary

    messages = [m.get("Value") for m in response.get("Messages", []) if m.get("Value")]

    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "period_seconds": cfg.metric_period_seconds,
        "metrics": metrics,
        "derived": _derive(metrics),
        "warnings": messages,
        "missing_metrics": sorted(set(labels.values()) - set(metrics)),
    }


def _derive(metrics: dict[str, Any]) -> dict[str, Any]:
    """Cheap ratios the model would otherwise have to compute from raw numbers."""
    derived: dict[str, Any] = {}

    errors = metrics.get("alb_5xx_count", {}).get("total")
    requests = metrics.get("alb_request_count", {}).get("total")
    if errors is not None and requests:
        derived["error_rate_pct"] = round(100 * errors / requests, 2)

    cpu = metrics.get("cpu_utilization_max_pct", {}).get("max")
    mem = metrics.get("memory_utilization_max_pct", {}).get("max")
    if cpu is not None:
        derived["cpu_saturated"] = cpu >= 85
    if mem is not None:
        derived["memory_saturated"] = mem >= 85
    if cpu is not None and mem is not None:
        # Errors without resource pressure point away from capacity as the cause.
        derived["resource_pressure"] = cpu >= 85 or mem >= 85

    unhealthy = metrics.get("alb_unhealthy_hosts", {}).get("max")
    if unhealthy is not None:
        derived["has_unhealthy_targets"] = unhealthy > 0

    return derived
