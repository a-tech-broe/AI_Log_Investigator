from __future__ import annotations

import httpx
import respx

from notifications import slack

WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


def _text_of(blocks):
    return " ".join(
        str(block.get("text", {}).get("text", ""))
        + " ".join(str(f.get("text", "")) for f in block.get("fields", []))
        + " ".join(str(e.get("text", "")) for e in block.get("elements", []))
        for block in blocks
    )


def test_card_renders_the_readme_fields(alert, report, evidence):
    blocks = slack.build_blocks(alert, report, evidence)
    text = _text_of(blocks)

    assert blocks[0]["type"] == "header"
    assert "AI Incident Summary" in blocks[0]["text"]["text"]
    assert "payment-api" in text
    assert "Critical" in text
    assert "91%" in text
    assert "Roll back to task definition payment-api:41" in text
    assert "Checkout is failing" in text


def test_confidence_bar_scales():
    assert slack._confidence_bar(100) == "█" * 10
    assert slack._confidence_bar(0) == "░" * 10
    assert slack._confidence_bar(50).count("█") == 5


def test_footer_lists_available_sources_and_model(alert, report, evidence):
    footer = slack.build_blocks(alert, report, evidence)[-1]["elements"][0]["text"]

    assert "ecs" in footer and "cloudwatch" in footer and "splunk" in footer
    assert "grafana" not in footer  # unavailable in the fixture
    assert "anthropic.claude-opus-5" in footer


def test_degraded_report_is_flagged_in_the_footer(alert, evidence):
    degraded = {
        "root_cause": "AI analysis unavailable",
        "confidence": 0,
        "severity": "high",
        "category": "unknown",
        "evidence": ["x"],
        "recommendations": [],
        "business_impact": "Unknown",
        "missing_evidence": [],
        "_meta": {"degraded": True},
    }

    footer = slack.build_blocks(alert, degraded, evidence)[-1]["elements"][0]["text"]

    assert "degraded" in footer


def test_action_buttons_link_to_grafana(alert, report, evidence):
    actions = [b for b in slack.build_blocks(alert, report, evidence) if b["type"] == "actions"]

    urls = [e["url"] for e in actions[0]["elements"]]
    assert alert.dashboard_url in urls
    assert alert.silence_url in urls


def test_blocks_are_capped_to_keep_the_card_readable(alert, report, evidence):
    report["evidence"] = [f"observation {i}" for i in range(20)]
    report["recommendations"] = [
        {"action": f"do {i}", "rationale": "r", "urgency": "soon"} for i in range(20)
    ]

    text = _text_of(slack.build_blocks(alert, report, evidence))

    assert "observation 0" in text
    assert f"observation {slack.MAX_EVIDENCE_ITEMS}" not in text
    assert f"do {slack.MAX_RECOMMENDATIONS}" not in text


def test_long_strings_are_truncated(alert, report, evidence):
    report["root_cause"] = "y" * 5000

    blocks = slack.build_blocks(alert, report, evidence)

    cause = next(b for b in blocks if "Likely Cause" in str(b.get("text", {}).get("text", "")))
    assert len(cause["text"]["text"]) < 1600


@respx.mock
def test_post_delivers_via_webhook(cfg, alert, report, evidence, secrets):
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200, text="ok"))

    result = slack.post(cfg, alert, report, evidence)

    assert result == {"delivered": True, "transport": "webhook"}
    body = route.calls[0].request.content.decode()
    assert "payment-api" in body


@respx.mock
def test_post_falls_back_to_bot_token(cfg, alert, report, evidence, monkeypatch):
    monkeypatch.setattr(slack, "load_secrets", lambda: {"slack_bot_token": "xoxb-test"})
    route = respx.post(slack.SLACK_API_URL).mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1700000000.0001"})
    )

    result = slack.post(cfg, alert, report, evidence)

    assert result["delivered"] is True
    assert result["ts"] == "1700000000.0001"
    assert route.calls[0].request.headers["Authorization"] == "Bearer xoxb-test"


@respx.mock
def test_slack_api_ok_false_is_treated_as_failure(cfg, alert, report, evidence, monkeypatch):
    monkeypatch.setattr(slack, "load_secrets", lambda: {"slack_bot_token": "xoxb-test"})
    respx.post(slack.SLACK_API_URL).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )

    result = slack.post(cfg, alert, report, evidence)

    assert result == {"delivered": False, "reason": "channel_not_found"}


@respx.mock
def test_transport_failure_never_raises(cfg, alert, report, evidence, secrets):
    respx.post(WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))

    result = slack.post(cfg, alert, report, evidence)

    assert result["delivered"] is False
    assert "ConnectError" in result["reason"]


def test_no_credentials_skips_cleanly(cfg, alert, report, evidence, monkeypatch):
    monkeypatch.setattr(slack, "load_secrets", dict)

    assert slack.post(cfg, alert, report, evidence) == {"delivered": False, "reason": "no_credentials"}


def test_dry_run_returns_blocks_without_posting(alert, report, evidence, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from utils import config as config_module

    config_module.reset_caches()

    result = slack.post(config_module.load_config(), alert, report, evidence)

    assert result["delivered"] is False
    assert result["reason"] == "dry_run"
    assert result["blocks"][0]["type"] == "header"
