"""Build the SRE investigation prompt and the schema its answer must satisfy."""

from __future__ import annotations

import json
from typing import Any

from utils.parser import Alert

SYSTEM_PROMPT = """\
You are a Senior Site Reliability Engineer on call for a production AWS ECS platform.

You receive an alert plus evidence already gathered from ECS, CloudWatch, Splunk, and
Grafana. Your job is to determine the most likely root cause and the action that will
restore service fastest.

How to reason about the evidence:
- Correlate across sources. A deployment minutes before the alert, tasks restarting, and
  a spike in one error pattern is a far stronger signal than any of those alone.
- Weigh absence of evidence. Errors with flat CPU and memory point away from capacity;
  restarts with OOMKilled point straight at memory limits.
- A collector marked `available: false` means the data source failed, not that the
  system is healthy. Say so rather than reasoning as if the signal were negative.
- Prefer the simplest cause that explains every observation over an elaborate one that
  explains a few.

Calibrate confidence honestly. Reserve 90+ for cases where the evidence is close to
conclusive, use 60-80 when the story is plausible but a key signal is missing, and go
below 50 when you are largely guessing — an accurate low number is far more useful to
the responder than a confident wrong answer.

Every claim in `evidence` must cite something in the input. Do not invent metrics,
counts, timestamps, or log lines. If the evidence is too thin to diagnose, say that in
`root_cause` and set a low confidence rather than manufacturing a story.

Recommendations are ordered actions a responder can take right now, most impactful
first. Be concrete: "roll back to task definition payment-api:41" beats "consider a
rollback".
"""

# Structured outputs guarantee the Slack formatter gets every field it renders.
INCIDENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root_cause": {
            "type": "string",
            "description": "The single most likely root cause, in one or two sentences.",
        },
        "confidence": {
            "type": "integer",
            "description": "Calibrated confidence in the root cause, 0-100.",
        },
        "severity": {
            "type": "string",
            "enum": ["critical", "high", "medium", "low"],
            "description": "Severity of the incident based on the evidence, not the alert label.",
        },
        "category": {
            "type": "string",
            "enum": [
                "deployment",
                "resource_exhaustion",
                "dependency_failure",
                "configuration",
                "traffic_spike",
                "infrastructure",
                "application_bug",
                "unknown",
            ],
        },
        "evidence": {
            "type": "array",
            "description": "Specific observations supporting the root cause, each traceable to the input.",
            "items": {"type": "string"},
        },
        "recommendations": {
            "type": "array",
            "description": "Ordered remediation actions, most impactful first.",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["immediate", "soon", "follow_up"]},
                },
                "required": ["action", "rationale", "urgency"],
                "additionalProperties": False,
            },
        },
        "business_impact": {
            "type": "string",
            "description": "What users are experiencing right now, in plain language.",
        },
        "missing_evidence": {
            "type": "array",
            "description": "Data that was unavailable or would sharpen the diagnosis.",
            "items": {"type": "string"},
        },
    },
    "required": [
        "root_cause",
        "confidence",
        "severity",
        "category",
        "evidence",
        "recommendations",
        "business_impact",
        "missing_evidence",
    ],
    "additionalProperties": False,
}


def _section(title: str, body: Any) -> str:
    rendered = body if isinstance(body, str) else json.dumps(body, indent=2, default=str)
    return f"<{title}>\n{rendered}\n</{title}>"


def _alert_section(alert: Alert) -> str:
    lines = [
        f"name: {alert.name}",
        f"service: {alert.service}",
        f"severity_label: {alert.severity}",
        f"status: {alert.status}",
        f"started_at: {alert.started_at.isoformat()}",
    ]
    if alert.cluster:
        lines.append(f"cluster: {alert.cluster}")
    if alert.summary:
        lines.append(f"summary: {alert.summary}")
    if alert.description:
        lines.append(f"description: {alert.description}")
    if alert.value:
        lines.append(f"triggering_value: {alert.value}")
    return "\n".join(lines)


def build_user_prompt(alert: Alert, evidence: dict[str, Any]) -> str:
    """Assemble the evidence packet the model reasons over."""
    parts = [
        _section("alert", _alert_section(alert)),
        _section("ecs_evidence", evidence.get("ecs", {"available": False})),
        _section("cloudwatch_evidence", evidence.get("cloudwatch", {"available": False})),
        _section("splunk_evidence", evidence.get("splunk", {"available": False})),
    ]

    grafana = evidence.get("grafana")
    if grafana and grafana.get("available"):
        parts.append(_section("grafana_evidence", grafana))

    parts.append(
        "Determine the root cause, your confidence in it, the supporting evidence, "
        "the recommended actions, and the business impact. Respond with JSON matching "
        "the required schema."
    )
    return "\n\n".join(parts)


def build_messages(alert: Alert, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"role": "user", "content": build_user_prompt(alert, evidence)}]
