"""Optional Grafana context: annotations and deploy markers around the alert."""

from __future__ import annotations

from typing import Any

import httpx

from collectors import collector
from utils.config import Config, load_secrets
from utils.logger import get_logger
from utils.parser import Alert

log = get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
MAX_ANNOTATIONS = 20


@collector("grafana")
def collect(cfg: Config, alert: Alert) -> dict[str, Any]:
    """Fetch annotations in the alert window — deploy markers, manual notes, state changes."""
    if not cfg.grafana_url:
        return {"available": False, "error": "GRAFANA_URL not configured"}

    token = load_secrets().get("grafana_token")
    if not token:
        return {"available": False, "error": "grafana_token missing from secret"}

    start, end = alert.window(cfg.lookback_minutes)
    response = httpx.get(
        f"{cfg.grafana_url}/api/annotations",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "from": int(start.timestamp() * 1000),
            "to": int(end.timestamp() * 1000),
            "limit": MAX_ANNOTATIONS,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, list):
        return {"available": False, "error": "unexpected annotations payload"}

    annotations = [
        {
            "time": item.get("time"),
            "text": item.get("text"),
            "tags": item.get("tags", []),
            "type": "alert" if item.get("alertId") else "annotation",
        }
        for item in payload
        if isinstance(item, dict)
    ]

    deploy_markers = [
        a
        for a in annotations
        if any("deploy" in str(tag).lower() or "release" in str(tag).lower() for tag in a["tags"])
    ]

    return {
        "annotation_count": len(annotations),
        "annotations": annotations,
        "deploy_markers": deploy_markers,
        "alert_name": alert.name,
        "dashboard_url": alert.dashboard_url,
    }
