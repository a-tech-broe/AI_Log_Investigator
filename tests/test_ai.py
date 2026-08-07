from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ai import bedrock, prompt

# --- prompt construction ------------------------------------------------------


def test_prompt_includes_every_available_source(alert, evidence):
    text = prompt.build_user_prompt(alert, evidence)

    assert "<alert>" in text
    assert "<ecs_evidence>" in text
    assert "<cloudwatch_evidence>" in text
    assert "<splunk_evidence>" in text
    assert "payment-api" in text
    assert "connection_pool_exhausted" in text


def test_unavailable_grafana_section_is_omitted(alert, evidence):
    assert "<grafana_evidence>" not in prompt.build_user_prompt(alert, evidence)


def test_available_grafana_section_is_included(alert, evidence):
    evidence["grafana"] = {"available": True, "deploy_markers": [{"text": "deploy payment-api:42"}]}

    assert "<grafana_evidence>" in prompt.build_user_prompt(alert, evidence)


def test_schema_forbids_extra_fields_and_requires_the_card_fields():
    schema = prompt.INCIDENT_SCHEMA

    assert schema["additionalProperties"] is False
    for field in ("root_cause", "confidence", "severity", "evidence", "recommendations", "business_impact"):
        assert field in schema["required"]

    rec = schema["properties"]["recommendations"]["items"]
    assert rec["required"] == ["action", "rationale", "urgency"]
    assert rec["additionalProperties"] is False


def test_build_messages_returns_a_single_user_turn(alert, evidence):
    messages = prompt.build_messages(alert, evidence)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"


# --- bedrock ------------------------------------------------------------------


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def _response(body, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(body))],
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=4200, output_tokens=610),
    )


@pytest.fixture
def patch_bedrock(monkeypatch):
    def _patch(response):
        client = FakeClient(response)
        monkeypatch.setattr(bedrock, "_client", lambda _region: client)
        return client

    return _patch


def test_analyze_returns_parsed_report(cfg, alert, evidence, report, patch_bedrock):
    patch_bedrock(_response(report))

    result = bedrock.analyze(cfg, alert, evidence)

    assert result["root_cause"] == report["root_cause"]
    assert result["confidence"] == 91
    assert result["_meta"]["model"] == "anthropic.claude-opus-5"
    assert result["_meta"]["input_tokens"] == 4200


def test_analyze_sends_schema_and_effort(cfg, alert, evidence, report, patch_bedrock):
    client = patch_bedrock(_response(report))

    bedrock.analyze(cfg, alert, evidence)

    kwargs = client.messages.kwargs
    assert kwargs["model"] == "anthropic.claude-opus-5"
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["effort"] == "high"
    assert kwargs["output_config"]["format"]["schema"] is prompt.INCIDENT_SCHEMA
    assert kwargs["system"] == prompt.SYSTEM_PROMPT
    assert kwargs["max_tokens"] == cfg.bedrock_max_tokens


def test_refusal_raises_analysis_error(cfg, alert, evidence, patch_bedrock):
    patch_bedrock(
        SimpleNamespace(
            content=[],
            stop_reason="refusal",
            stop_details=SimpleNamespace(category="cyber"),
            usage=None,
        )
    )

    with pytest.raises(bedrock.AnalysisError, match="declined"):
        bedrock.analyze(cfg, alert, evidence)


def test_truncated_response_raises_rather_than_returning_partial_json(cfg, alert, evidence, patch_bedrock):
    patch_bedrock(_response({"root_cause": "x"}, stop_reason="max_tokens"))

    with pytest.raises(bedrock.AnalysisError, match="max_tokens"):
        bedrock.analyze(cfg, alert, evidence)


def test_missing_text_block_raises(cfg, alert, evidence, patch_bedrock):
    patch_bedrock(
        SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="")],
            stop_reason="end_turn",
            stop_details=None,
            usage=None,
        )
    )

    with pytest.raises(bedrock.AnalysisError, match="no text block"):
        bedrock.analyze(cfg, alert, evidence)


# --- fallback -----------------------------------------------------------------


def test_fallback_report_matches_the_schema_contract(alert, evidence):
    result = bedrock.fallback_report(alert, evidence, "timeout")

    for field in prompt.INCIDENT_SCHEMA["required"]:
        assert field in result
    assert result["confidence"] == 0
    assert result["_meta"]["degraded"] is True
    assert result["recommendations"][0]["urgency"] == "immediate"


def test_fallback_report_surfaces_raw_signals(alert, evidence):
    result = bedrock.fallback_report(alert, evidence, "timeout")
    joined = " ".join(result["evidence"])

    assert "desired=6" in joined
    assert "connection_pool_exhausted" in joined
    assert "3 stopped tasks" in joined


def test_fallback_report_handles_total_collection_failure(alert):
    empty = {name: {"available": False} for name in ("ecs", "cloudwatch", "splunk")}

    result = bedrock.fallback_report(alert, empty, "no data")

    assert result["evidence"] == ["No evidence could be collected."]
    assert len(result["missing_evidence"]) == 3
