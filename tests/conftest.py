"""Shared fixtures. Simulated production incidents live here."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from utils import config as config_module
from utils.parser import Alert

FIXTURES = Path(__file__).parent / "fixtures"

DEFAULT_ENV = {
    "AWS_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "ENVIRONMENT": "test",
    "ECS_CLUSTER": "prod-ecs",
    "SPLUNK_HOST": "https://splunk.example.com:8089",
    "SPLUNK_INDEX": "prod",
    "GRAFANA_URL": "https://grafana.example.com",
    "BEDROCK_MODEL_ID": "anthropic.claude-opus-5",
    "SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:ali-test",
    "EVIDENCE_BUCKET": "",
    "SNS_TOPIC_ARN": "",
    "LOOKBACK_MINUTES": "15",
    "ALB_ARN_SUFFIX": "app/prod-alb/1234567890abcdef",
    "TARGET_GROUP_ARN_SUFFIX": "targetgroup/payment-api/abcdef1234567890",
    "LOG_LEVEL": "DEBUG",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a clean, fully-populated environment."""
    for key, value in DEFAULT_ENV.items():
        monkeypatch.setenv(key, value)
    config_module.reset_caches()
    yield
    config_module.reset_caches()


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Stub Secrets Manager so no test needs AWS credentials."""
    values = {
        "splunk_token": "splunk-test-token",
        "slack_webhook_url": "https://hooks.slack.com/services/T000/B000/XXXX",
        "grafana_token": "grafana-test-token",
    }
    monkeypatch.setattr(config_module, "load_secrets", lambda: values)
    for module_path in (
        "collectors.splunk",
        "collectors.grafana",
        "notifications.slack",
    ):
        module = __import__(module_path, fromlist=["load_secrets"])
        monkeypatch.setattr(module, "load_secrets", lambda: values)
    return values


@pytest.fixture
def cfg():
    return config_module.load_config()


@pytest.fixture
def event() -> dict[str, Any]:
    return json.loads((FIXTURES / "grafana_alert.json").read_text())


@pytest.fixture
def alert() -> Alert:
    return Alert(
        name="HighErrorRate",
        service="payment-api",
        severity="critical",
        status="firing",
        started_at=datetime.now(UTC) - timedelta(minutes=2),
        cluster="prod-ecs",
        summary="payment-api HTTP 500 rate above 5%",
        description="Error rate is 8.42% (threshold 5%).",
        value="8.42",
        dashboard_url="https://grafana.example.com/d/abc123/payment-api",
        silence_url="https://grafana.example.com/alerting/silence/new",
        labels={"alertname": "HighErrorRate", "service": "payment-api"},
    )


@pytest.fixture
def evidence() -> dict[str, Any]:
    """A realistic 'bad deploy exhausted the DB pool' incident."""
    return {
        "ecs": {
            "available": True,
            "error": None,
            "source": "ecs",
            "cluster": "prod-ecs",
            "service": "payment-api",
            "desired_count": 6,
            "running_count": 4,
            "pending_count": 2,
            "capacity_shortfall": 2,
            "is_healthy": False,
            "recent_deployment_minutes_ago": 8.0,
            "deployment_in_progress": True,
            "stopped_task_count": 3,
            "failure_classes": ["essential_container_exit"],
            "stopped_tasks": [
                {
                    "task_id": "abc123",
                    "stopped_reason": "Essential container in task exited",
                    "failure_class": "essential_container_exit",
                    "stopped_minutes_ago": 4.0,
                    "containers": [{"name": "app", "exit_code": 137, "reason": None}],
                }
            ],
            "deployments": [
                {
                    "status": "PRIMARY",
                    "rollout_state": "IN_PROGRESS",
                    "task_definition": "payment-api:42",
                    "created_minutes_ago": 8.0,
                }
            ],
            "service_events": [],
        },
        "cloudwatch": {
            "available": True,
            "error": None,
            "source": "cloudwatch",
            "metrics": {
                "cpu_utilization_max_pct": {
                    "latest": 28.0,
                    "max": 31.2,
                    "avg": 27.5,
                    "min": 22.0,
                    "datapoints": 15,
                },
                "memory_utilization_max_pct": {
                    "latest": 41.0,
                    "max": 44.0,
                    "avg": 40.1,
                    "min": 38.0,
                    "datapoints": 15,
                },
                "alb_5xx_count": {
                    "latest": 42.0,
                    "max": 61.0,
                    "avg": 38.0,
                    "min": 12.0,
                    "total": 570.0,
                    "datapoints": 15,
                },
                "alb_request_count": {
                    "latest": 500.0,
                    "max": 620.0,
                    "avg": 510.0,
                    "min": 400.0,
                    "total": 7650.0,
                    "datapoints": 15,
                },
            },
            "derived": {"error_rate_pct": 7.45, "cpu_saturated": False, "memory_saturated": False},
            "warnings": [],
            "missing_metrics": [],
        },
        "splunk": {
            "available": True,
            "error": None,
            "source": "splunk",
            "total_events_scanned": 312,
            "dominant_pattern": "connection_pool_exhausted",
            "patterns": [
                {
                    "pattern": "connection_pool_exhausted",
                    "severity": "critical",
                    "count": 217,
                    "share_pct": 69.6,
                    "distinct_hosts": 4,
                    "top_hosts": ["ip-10-0-1-5"],
                    "examples": [
                        {
                            "time": "2026-08-07T10:35:10Z",
                            "host": "ip-10-0-1-5",
                            "message": "HikariPool-1 - Connection is not available, request timed out after 30000ms",
                        }
                    ],
                },
                {
                    "pattern": "timeout",
                    "severity": "high",
                    "count": 63,
                    "share_pct": 20.2,
                    "distinct_hosts": 3,
                    "top_hosts": ["ip-10-0-1-6"],
                    "examples": [],
                },
            ],
            "truncated": False,
        },
        "grafana": {"available": False, "error": "GRAFANA_URL not configured", "source": "grafana"},
        "_meta": {"elapsed_seconds": 3.1, "available_sources": ["ecs", "cloudwatch", "splunk"]},
    }


@pytest.fixture
def report() -> dict[str, Any]:
    return {
        "root_cause": "Deployment payment-api:42 exhausted the database connection pool.",
        "confidence": 91,
        "severity": "critical",
        "category": "deployment",
        "evidence": [
            "Deployment started 8 minutes before the alert",
            "217 connection-pool-exhausted log events (69.6% of errors)",
            "CPU 31% and memory 44% rule out resource saturation",
        ],
        "recommendations": [
            {
                "action": "Roll back to task definition payment-api:41",
                "rationale": "Errors began within minutes of the payment-api:42 rollout",
                "urgency": "immediate",
            },
            {
                "action": "Raise the HikariCP pool ceiling and add a saturation alarm",
                "rationale": "Prevents recurrence once the rollout resumes",
                "urgency": "follow_up",
            },
        ],
        "business_impact": "Checkout is failing for roughly 7% of requests.",
        "missing_evidence": ["Grafana annotations unavailable"],
        "_meta": {"model": "anthropic.claude-opus-5", "input_tokens": 4200, "output_tokens": 610},
    }
