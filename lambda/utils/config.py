"""Environment-driven configuration and secret resolution."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from utils.logger import get_logger

log = get_logger(__name__)

# Shared botocore config: incident collection must never hang the Lambda.
BOTO_CONFIG = BotoConfig(
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=5,
    read_timeout=15,
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("invalid integer for %s, using default", name, extra={"raw": raw})
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """All runtime knobs, resolved once per cold start."""

    aws_region: str
    environment: str

    # ECS
    ecs_cluster: str
    ecs_stopped_task_limit: int

    # CloudWatch
    alb_arn_suffix: str
    target_group_arn_suffix: str
    metric_period_seconds: int

    # Splunk
    splunk_host: str
    splunk_index: str
    splunk_max_events: int
    splunk_verify_tls: bool

    # Grafana
    grafana_url: str

    # Bedrock
    bedrock_model_id: str
    bedrock_effort: str
    bedrock_max_tokens: int

    # Slack
    slack_channel: str

    # Investigation window
    lookback_minutes: int

    # Storage / notification plumbing
    secret_arn: str
    evidence_bucket: str
    sns_topic_arn: str

    log_level: str
    dry_run: bool

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def lookback_seconds(self) -> int:
        return self.lookback_minutes * 60


@lru_cache(maxsize=1)
def load_config() -> Config:
    return Config(
        aws_region=_env("AWS_REGION", "us-east-1"),
        environment=_env("ENVIRONMENT", "prod"),
        ecs_cluster=_env("ECS_CLUSTER"),
        ecs_stopped_task_limit=_env_int("ECS_STOPPED_TASK_LIMIT", 10),
        alb_arn_suffix=_env("ALB_ARN_SUFFIX"),
        target_group_arn_suffix=_env("TARGET_GROUP_ARN_SUFFIX"),
        metric_period_seconds=_env_int("METRIC_PERIOD_SECONDS", 60),
        splunk_host=_env("SPLUNK_HOST").rstrip("/"),
        splunk_index=_env("SPLUNK_INDEX", "prod"),
        splunk_max_events=_env_int("SPLUNK_MAX_EVENTS", 500),
        splunk_verify_tls=_env_bool("SPLUNK_VERIFY_TLS", True),
        grafana_url=_env("GRAFANA_URL").rstrip("/"),
        bedrock_model_id=_env("BEDROCK_MODEL_ID", "anthropic.claude-opus-5"),
        bedrock_effort=_env("BEDROCK_EFFORT", "high"),
        bedrock_max_tokens=_env_int("BEDROCK_MAX_TOKENS", 16000),
        slack_channel=_env("SLACK_CHANNEL", "#incidents"),
        lookback_minutes=_env_int("LOOKBACK_MINUTES", 15),
        secret_arn=_env("SECRET_ARN"),
        evidence_bucket=_env("EVIDENCE_BUCKET"),
        sns_topic_arn=_env("SNS_TOPIC_ARN"),
        log_level=_env("LOG_LEVEL", "INFO"),
        dry_run=_env_bool("DRY_RUN", False),
    )


@lru_cache(maxsize=1)
def load_secrets() -> dict[str, str]:
    """Fetch the single JSON secret holding third-party credentials.

    Expected keys (all optional — a missing key disables that integration):
        splunk_token, slack_webhook_url, slack_bot_token, grafana_token
    """
    cfg = load_config()
    if not cfg.secret_arn:
        log.warning("SECRET_ARN not set; third-party integrations are disabled")
        return {}

    client = boto3.client("secretsmanager", region_name=cfg.aws_region, config=BOTO_CONFIG)
    response = client.get_secret_value(SecretId=cfg.secret_arn)
    raw = response.get("SecretString") or "{}"

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.error("secret payload is not valid JSON")
        return {}

    if not isinstance(parsed, dict):
        log.error("secret payload is not a JSON object")
        return {}

    return {str(k): str(v) for k, v in parsed.items() if v is not None}


def reset_caches() -> None:
    """Test hook — clears memoized config and secrets.

    Tolerates either function being monkeypatched with a plain callable, which
    is how tests stub out Secrets Manager.
    """
    for fn in (load_config, load_secrets):
        clear = getattr(fn, "cache_clear", None)
        if clear is not None:
            clear()
