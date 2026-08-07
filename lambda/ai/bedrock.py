"""Claude inference on Amazon Bedrock via the Anthropic SDK."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from anthropic import AnthropicBedrockMantle

from ai.prompt import INCIDENT_SCHEMA, SYSTEM_PROMPT, build_messages
from utils.config import Config
from utils.logger import get_logger
from utils.parser import Alert

log = get_logger(__name__)


class AnalysisError(RuntimeError):
    """Raised when Bedrock returns something we cannot turn into a report."""


@lru_cache(maxsize=4)
def _client(region: str) -> AnthropicBedrockMantle:
    # Cached across warm invocations so the HTTP connection pool is reused.
    return AnthropicBedrockMantle(aws_region=region)


def _extract_json(content: list[Any]) -> dict[str, Any]:
    """Pull the structured-output payload out of the response content blocks."""
    for block in content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "") or ""
            if text.strip():
                return json.loads(text)
    raise AnalysisError("response contained no text block")


def analyze(cfg: Config, alert: Alert, evidence: dict[str, Any]) -> dict[str, Any]:
    """Ask Claude for a structured incident report.

    The response is constrained by `INCIDENT_SCHEMA`, so callers can rely on
    every required field being present.
    """
    client = _client(cfg.aws_region)
    messages = build_messages(alert, evidence)

    response = client.messages.create(
        model=cfg.bedrock_model_id,
        max_tokens=cfg.bedrock_max_tokens,
        system=SYSTEM_PROMPT,
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={
            "effort": cfg.bedrock_effort,
            "format": {"type": "json_schema", "schema": INCIDENT_SCHEMA},
        },
    )

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise AnalysisError(f"model declined the request (category={getattr(details, 'category', None)})")

    if response.stop_reason == "max_tokens":
        # Structured output was cut mid-JSON; nothing parseable survives.
        raise AnalysisError("response hit max_tokens before completing the report")

    report = _extract_json(response.content)

    usage = getattr(response, "usage", None)
    log.info(
        "bedrock analysis complete",
        extra={
            "model": cfg.bedrock_model_id,
            "stop_reason": response.stop_reason,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "confidence": report.get("confidence"),
            "category": report.get("category"),
        },
    )

    report["_meta"] = {
        "model": cfg.bedrock_model_id,
        "effort": cfg.bedrock_effort,
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }
    return report


def fallback_report(alert: Alert, evidence: dict[str, Any], reason: str) -> dict[str, Any]:
    """A schema-shaped report for when inference fails.

    The responder still gets the raw signals and a working Slack card instead of
    silence, which matters more during an incident than a perfect diagnosis.
    """
    observations: list[str] = []

    ecs = evidence.get("ecs", {})
    if ecs.get("available"):
        observations.append(
            f"ECS {ecs.get('service')}: desired={ecs.get('desired_count')} "
            f"running={ecs.get('running_count')} pending={ecs.get('pending_count')}"
        )
        if ecs.get("stopped_task_count"):
            observations.append(f"{ecs['stopped_task_count']} stopped tasks in the window")

    splunk = evidence.get("splunk", {})
    if splunk.get("available") and splunk.get("patterns"):
        top = splunk["patterns"][0]
        observations.append(f"Top log pattern: {top['pattern']} ({top['count']} events)")

    cw = evidence.get("cloudwatch", {})
    if cw.get("available"):
        for name in ("cpu_utilization_max_pct", "memory_utilization_max_pct", "alb_5xx_count"):
            metric = cw.get("metrics", {}).get(name)
            if metric:
                observations.append(f"{name}: max={metric.get('max')}")

    unavailable = [
        name for name in ("ecs", "cloudwatch", "splunk") if not evidence.get(name, {}).get("available")
    ]

    return {
        "root_cause": f"AI analysis unavailable ({reason}). Raw evidence is included below.",
        "confidence": 0,
        "severity": alert.severity if alert.severity in {"critical", "high", "medium", "low"} else "high",
        "category": "unknown",
        "evidence": observations or ["No evidence could be collected."],
        "recommendations": [
            {
                "action": "Investigate manually using the collected evidence",
                "rationale": f"Automated analysis failed: {reason}",
                "urgency": "immediate",
            }
        ],
        "business_impact": f"Unknown — {alert.service} alerted with '{alert.name}'.",
        "missing_evidence": [f"{name} collector unavailable" for name in unavailable] or ["AI analysis"],
        "_meta": {"degraded": True, "reason": reason},
    }
