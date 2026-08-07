"""Parse Grafana alerts delivered through EventBridge into a normalized Alert."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)

# Grafana lets operators name the service label almost anything; check in order.
_SERVICE_LABELS = (
    "service",
    "service_name",
    "app",
    "application",
    "ecs_service",
    "job",
    "container",
)
_CLUSTER_LABELS = ("cluster", "ecs_cluster", "cluster_name")
_SEVERITY_LABELS = ("severity", "priority", "level")

_SEVERITY_ALIASES = {
    "p1": "critical",
    "sev1": "critical",
    "crit": "critical",
    "critical": "critical",
    "page": "critical",
    "p2": "high",
    "sev2": "high",
    "high": "high",
    "error": "high",
    "p3": "warning",
    "sev3": "warning",
    "warn": "warning",
    "warning": "warning",
    "p4": "info",
    "info": "info",
    "low": "info",
}


class AlertParseError(ValueError):
    """Raised when an event carries nothing we can investigate."""


@dataclass
class Alert:
    """Normalized view of the alert that triggered this investigation."""

    name: str
    service: str
    severity: str
    status: str
    started_at: datetime
    cluster: str = ""
    summary: str = ""
    description: str = ""
    value: str = ""
    dashboard_url: str = ""
    panel_url: str = ""
    silence_url: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""

    @property
    def is_firing(self) -> bool:
        return self.status.lower() == "firing"

    def window(self, lookback_minutes: int) -> tuple[datetime, datetime]:
        """Investigation window: `lookback_minutes` before the alert fired, to now.

        Anchoring on `started_at` keeps the evidence relevant even when the
        Lambda runs minutes after the alert (retries, throttling, cold starts).
        """
        now = datetime.now(UTC)
        start = self.started_at - timedelta(minutes=lookback_minutes)
        end = max(now, self.started_at + timedelta(minutes=1))
        return start, end

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        return payload


def _first_label(labels: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = labels.get(key)
        if value:
            return str(value).strip()
    return ""


def _normalize_severity(raw: str) -> str:
    if not raw:
        return "unknown"
    return _SEVERITY_ALIASES.get(raw.strip().lower(), raw.strip().lower())


def _parse_timestamp(raw: Any) -> datetime:
    """Best-effort ISO-8601 parse; falls back to now so we never drop an alert."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)

    if isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        # Grafana emits nanosecond precision; datetime accepts at most microseconds.
        text = re.sub(r"(\.\d{6})\d+", r"\1", text)
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.year > 1970:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            log.warning("unparseable timestamp, defaulting to now", extra={"raw": raw})

    return datetime.now(UTC)


def _unwrap(event: dict[str, Any]) -> dict[str, Any]:
    """Strip the EventBridge envelope to reach the Grafana webhook payload."""
    if not isinstance(event, dict):
        raise AlertParseError(f"event must be a mapping, got {type(event).__name__}")

    detail = event.get("detail", event)
    if not isinstance(detail, dict):
        raise AlertParseError("event.detail must be a mapping")

    # API Destinations / API Gateway can deliver the payload as a JSON string body.
    body = detail.get("body")
    if isinstance(body, dict):
        return body

    return detail


def _select_alert(payload: dict[str, Any]) -> dict[str, Any]:
    """Pick the alert to investigate: the first firing one, else the first."""
    alerts = payload.get("alerts")
    if isinstance(alerts, list) and alerts:
        firing = [a for a in alerts if isinstance(a, dict) and a.get("status") == "firing"]
        chosen = firing[0] if firing else alerts[0]
        if isinstance(chosen, dict):
            return chosen

    # Legacy Grafana / hand-rolled payloads have no `alerts` array.
    return payload


def parse_alert(event: dict[str, Any]) -> Alert:
    """Convert an EventBridge event carrying a Grafana alert into an `Alert`.

    Raises:
        AlertParseError: when no service can be identified — without a service
            there is nothing to query in ECS, CloudWatch, or Splunk.
    """
    payload = _unwrap(event)
    alert = _select_alert(payload)

    labels: dict[str, str] = {}
    for source in (payload.get("commonLabels"), alert.get("labels")):
        if isinstance(source, dict):
            labels.update({str(k): str(v) for k, v in source.items() if v is not None})

    annotations: dict[str, str] = {}
    for source in (payload.get("commonAnnotations"), alert.get("annotations")):
        if isinstance(source, dict):
            annotations.update({str(k): str(v) for k, v in source.items() if v is not None})

    service = _first_label(labels, _SERVICE_LABELS)
    if not service:
        raise AlertParseError("alert has no service label; expected one of " + ", ".join(_SERVICE_LABELS))

    name = labels.get("alertname") or payload.get("title") or annotations.get("summary") or "Unnamed alert"
    status = str(alert.get("status") or payload.get("status") or "firing")

    return Alert(
        name=str(name),
        service=service,
        severity=_normalize_severity(_first_label(labels, _SEVERITY_LABELS)),
        status=status,
        started_at=_parse_timestamp(alert.get("startsAt") or payload.get("startsAt")),
        cluster=_first_label(labels, _CLUSTER_LABELS),
        summary=annotations.get("summary", ""),
        description=annotations.get("description", ""),
        value=str(alert.get("valueString") or payload.get("valueString") or ""),
        dashboard_url=str(alert.get("dashboardURL") or payload.get("externalURL") or ""),
        panel_url=str(alert.get("panelURL") or ""),
        silence_url=str(alert.get("silenceURL") or ""),
        labels=labels,
        annotations=annotations,
        fingerprint=str(alert.get("fingerprint") or ""),
    )
