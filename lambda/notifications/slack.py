"""Render the incident report as a Slack Block Kit card and post it."""

from __future__ import annotations

from typing import Any

import httpx

from utils.config import Config, load_secrets
from utils.logger import get_logger
from utils.parser import Alert

log = get_logger(__name__)

POST_TIMEOUT_SECONDS = 10
SLACK_API_URL = "https://slack.com/api/chat.postMessage"

SEVERITY_EMOJI = {
    "critical": "🚨",
    "high": "🔥",
    "medium": "⚠️",
    "low": "ℹ️",
}
URGENCY_EMOJI = {"immediate": "⚡", "soon": "⏱️", "follow_up": "📋"}

MAX_EVIDENCE_ITEMS = 5
MAX_RECOMMENDATIONS = 4


def _confidence_bar(confidence: int) -> str:
    filled = max(0, min(10, round(confidence / 10)))
    return "█" * filled + "░" * (10 - filled)


def _truncate(text: str, limit: int) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_blocks(alert: Alert, report: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the Block Kit payload. Pure — unit-testable without Slack."""
    severity = str(report.get("severity", "high")).lower()
    confidence = int(report.get("confidence", 0) or 0)
    emoji = SEVERITY_EMOJI.get(severity, "🚨")

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} AI Incident Summary", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service*\n`{alert.service}`"},
                {"type": "mrkdwn", "text": f"*Severity*\n{severity.title()}"},
                {"type": "mrkdwn", "text": f"*Alert*\n{_truncate(alert.name, 120)}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Category*\n{str(report.get('category', 'unknown')).replace('_', ' ').title()}",
                },
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Likely Cause*\n{_truncate(report.get('root_cause', 'Unknown'), 1500)}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Confidence*\n`{_confidence_bar(confidence)}` {confidence}%",
            },
        },
    ]

    evidence_items = list(report.get("evidence") or [])[:MAX_EVIDENCE_ITEMS]
    if evidence_items:
        bullets = "\n".join(f"• {_truncate(item, 250)}" for item in evidence_items)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Evidence*\n{bullets}"}})

    recommendations = list(report.get("recommendations") or [])[:MAX_RECOMMENDATIONS]
    if recommendations:
        lines = []
        for rec in recommendations:
            if not isinstance(rec, dict):
                continue
            marker = URGENCY_EMOJI.get(str(rec.get("urgency", "")), "•")
            lines.append(
                f"{marker} *{_truncate(rec.get('action', ''), 200)}*\n     _{_truncate(rec.get('rationale', ''), 200)}_"
            )
        if lines:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "*Recommended*\n" + "\n".join(lines)}}
            )

    impact = report.get("business_impact")
    if impact:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Impact*\n{_truncate(impact, 800)}"}}
        )

    missing = list(report.get("missing_evidence") or [])
    if missing:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"⚠️ Gaps: {_truncate(', '.join(str(m) for m in missing), 400)}",
                    }
                ],
            }
        )

    if alert.dashboard_url or alert.silence_url:
        elements = []
        if alert.dashboard_url:
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Dashboard"},
                    "url": alert.dashboard_url,
                }
            )
        if alert.silence_url:
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Silence Alert"},
                    "url": alert.silence_url,
                }
            )
        blocks.append({"type": "actions", "elements": elements})

    sources = [
        name for name in ("ecs", "cloudwatch", "splunk", "grafana") if evidence.get(name, {}).get("available")
    ]
    meta = report.get("_meta", {})
    footer = f"Sources: {', '.join(sources) or 'none'}"
    if meta.get("model"):
        footer += f" · {meta['model']}"
    if meta.get("degraded"):
        footer += " · degraded (AI analysis unavailable)"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})

    return blocks


def _fallback_text(alert: Alert, report: dict[str, Any]) -> str:
    """Notification text shown in the sidebar and on mobile push."""
    return (
        f"{SEVERITY_EMOJI.get(str(report.get('severity', 'high')).lower(), '🚨')} "
        f"{alert.service}: {_truncate(report.get('root_cause', alert.name), 150)}"
    )


def post(cfg: Config, alert: Alert, report: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Post the incident card. Returns a delivery status dict; never raises."""
    blocks = build_blocks(alert, report, evidence)
    text = _fallback_text(alert, report)

    if cfg.dry_run:
        log.info("dry run: skipping Slack post", extra={"block_count": len(blocks)})
        return {"delivered": False, "reason": "dry_run", "blocks": blocks}

    secrets = load_secrets()
    webhook_url = secrets.get("slack_webhook_url")
    bot_token = secrets.get("slack_bot_token")

    try:
        if webhook_url:
            response = httpx.post(
                webhook_url,
                json={"text": text, "blocks": blocks},
                timeout=POST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return {"delivered": True, "transport": "webhook"}

        if bot_token:
            response = httpx.post(
                SLACK_API_URL,
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"channel": cfg.slack_channel, "text": text, "blocks": blocks},
                timeout=POST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            # Slack returns HTTP 200 with ok=false for API-level errors.
            if not body.get("ok"):
                log.error("slack api rejected message", extra={"slack_error": body.get("error")})
                return {"delivered": False, "reason": body.get("error", "unknown")}
            return {"delivered": True, "transport": "chat.postMessage", "ts": body.get("ts")}

        log.warning("no Slack credentials configured; skipping notification")
        return {"delivered": False, "reason": "no_credentials"}

    except Exception as exc:
        log.exception("failed to deliver Slack notification")
        return {"delivered": False, "reason": f"{type(exc).__name__}: {exc}"}
