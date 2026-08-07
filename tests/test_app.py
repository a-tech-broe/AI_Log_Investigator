"""End-to-end handler tests against simulated production incidents."""

from __future__ import annotations

import json
import os

import pytest

import app
from ai import bedrock
from notifications import slack


@pytest.fixture
def wired(monkeypatch, evidence, report):
    """Patch every external boundary; assert on orchestration, not on AWS."""
    calls: dict[str, object] = {}

    monkeypatch.setattr(app, "collect_evidence", lambda cfg, alert: evidence)

    def fake_analyze(cfg, alert, ev):
        calls["analyze"] = (alert.service, ev)
        return report

    def fake_post(cfg, alert, rep, ev):
        calls["slack"] = rep
        return {"delivered": True, "transport": "webhook"}

    monkeypatch.setattr(bedrock, "analyze", fake_analyze)
    monkeypatch.setattr(slack, "post", fake_post)
    return calls


def test_happy_path(event, wired):
    response = app.handler(event, None)

    assert response["statusCode"] == 200
    body = response["body"]
    assert body["service"] == "payment-api"
    assert body["confidence"] == 91
    assert body["severity"] == "critical"
    assert body["category"] == "deployment"
    assert body["slack"]["delivered"] is True
    assert body["degraded"] is False
    assert set(body["sources"]) == {"ecs", "cloudwatch", "splunk"}
    assert wired["analyze"][0] == "payment-api"


def test_resolved_alert_is_skipped(event, wired):
    event["detail"]["alerts"][0]["status"] = "resolved"

    response = app.handler(event, None)

    assert response["body"] == {"skipped": "alert_resolved", "service": "payment-api"}
    assert "analyze" not in wired


def test_unparseable_alert_returns_400_without_retrying(wired):
    response = app.handler({"detail": {"alerts": [{"labels": {"alertname": "Orphan"}}]}}, None)

    assert response["statusCode"] == 400
    assert "no service label" in response["body"]["error"]
    assert "analyze" not in wired


def test_analysis_failure_still_notifies_with_degraded_report(event, monkeypatch, evidence):
    monkeypatch.setattr(app, "collect_evidence", lambda cfg, alert: evidence)
    monkeypatch.setattr(
        bedrock, "analyze", lambda *_a: (_ for _ in ()).throw(bedrock.AnalysisError("bedrock down"))
    )
    posted: dict = {}

    def fake_post(cfg, alert, rep, ev):
        posted["report"] = rep
        return {"delivered": True, "transport": "webhook"}

    monkeypatch.setattr(slack, "post", fake_post)

    response = app.handler(event, None)

    assert response["statusCode"] == 200
    assert response["body"]["degraded"] is True
    assert response["body"]["confidence"] == 0
    assert "bedrock down" in posted["report"]["root_cause"]
    # The responder still sees the raw evidence.
    assert any("desired=6" in item for item in posted["report"]["evidence"])


def test_slack_failure_does_not_fail_the_invocation(event, monkeypatch, evidence, report):
    monkeypatch.setattr(app, "collect_evidence", lambda cfg, alert: evidence)
    monkeypatch.setattr(bedrock, "analyze", lambda *_a: report)
    monkeypatch.setattr(slack, "post", lambda *_a: {"delivered": False, "reason": "no_credentials"})

    response = app.handler(event, None)

    assert response["statusCode"] == 200
    assert response["body"]["slack"]["delivered"] is False


def test_unexpected_failure_reraises_for_eventbridge_retry(event, monkeypatch):
    monkeypatch.setattr(
        app, "collect_evidence", lambda *_a: (_ for _ in ()).throw(RuntimeError("catastrophe"))
    )

    with pytest.raises(RuntimeError, match="catastrophe"):
        app.handler(event, None)


def test_collect_evidence_runs_every_collector_and_tolerates_failure(cfg, alert, monkeypatch):
    monkeypatch.setitem(app.COLLECTORS, "ecs", lambda c, a: {"available": True, "service": a.service})
    monkeypatch.setitem(app.COLLECTORS, "cloudwatch", lambda c, a: {"available": True})
    monkeypatch.setitem(app.COLLECTORS, "splunk", lambda c, a: {"available": False, "error": "no token"})
    monkeypatch.setitem(
        app.COLLECTORS, "grafana", lambda c, a: (_ for _ in ()).throw(RuntimeError("grafana exploded"))
    )

    result = app.collect_evidence(cfg, alert)

    assert result["ecs"]["service"] == "payment-api"
    assert result["splunk"]["available"] is False
    assert result["grafana"]["available"] is False
    assert "grafana exploded" in result["grafana"]["error"]
    assert set(result["_meta"]["available_sources"]) == {"ecs", "cloudwatch"}


# --- archival and operator notification --------------------------------------


class FakeAws:
    """Captures put_object / publish calls without touching AWS."""

    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def put_object(self, **kwargs):
        if self.fail:
            raise RuntimeError("access denied")
        self.calls.append(kwargs)

    def publish(self, **kwargs):
        if self.fail:
            raise RuntimeError("topic gone")
        self.calls.append(kwargs)


@pytest.fixture
def patch_boto(monkeypatch):
    def _patch(client):
        monkeypatch.setattr(app.boto3, "client", lambda *_a, **_kw: client)
        return client

    return _patch


def _cfg_with(**env):
    from utils import config as config_module

    for key, value in env.items():
        os.environ[key] = value
    config_module.reset_caches()
    return config_module.load_config()


def test_persist_writes_a_partitioned_encrypted_object(alert, evidence, report, patch_boto):
    aws = patch_boto(FakeAws())
    cfg = _cfg_with(EVIDENCE_BUCKET="evidence-bucket")

    key = app._persist(cfg, alert, evidence, report)

    assert key is not None
    assert key.startswith("investigations/")
    assert alert.service in key
    written = aws.calls[0]
    assert written["Bucket"] == "evidence-bucket"
    assert written["ServerSideEncryption"] == "AES256"

    payload = json.loads(written["Body"])
    assert payload["report"]["confidence"] == 91
    assert payload["alert"]["service"] == "payment-api"
    assert payload["evidence"]["ecs"]["desired_count"] == 6


def test_persist_is_skipped_without_a_bucket(alert, evidence, report, patch_boto):
    aws = patch_boto(FakeAws())
    cfg = _cfg_with(EVIDENCE_BUCKET="")

    assert app._persist(cfg, alert, evidence, report) is None
    assert aws.calls == []


def test_persist_failure_is_swallowed(alert, evidence, report, patch_boto):
    patch_boto(FakeAws(fail=True))
    cfg = _cfg_with(EVIDENCE_BUCKET="evidence-bucket")

    # Archival is best-effort: losing the archive must not lose the report.
    assert app._persist(cfg, alert, evidence, report) is None


def test_operator_alert_publishes_to_sns(patch_boto):
    aws = patch_boto(FakeAws())
    cfg = _cfg_with(SNS_TOPIC_ARN="arn:aws:sns:us-east-1:123456789012:ops")

    app._alert_operators(cfg, "subject", "message body")

    assert aws.calls[0]["TopicArn"].endswith(":ops")
    assert aws.calls[0]["Message"] == "message body"


def test_operator_alert_truncates_long_subjects(patch_boto):
    aws = patch_boto(FakeAws())
    cfg = _cfg_with(SNS_TOPIC_ARN="arn:aws:sns:us-east-1:123456789012:ops")

    app._alert_operators(cfg, "x" * 300, "body")

    # SNS rejects subjects over 100 characters.
    assert len(aws.calls[0]["Subject"]) == 100


def test_operator_alert_skipped_without_a_topic(patch_boto):
    aws = patch_boto(FakeAws())
    cfg = _cfg_with(SNS_TOPIC_ARN="")

    app._alert_operators(cfg, "subject", "body")

    assert aws.calls == []


def test_operator_alert_failure_is_swallowed(patch_boto):
    patch_boto(FakeAws(fail=True))
    cfg = _cfg_with(SNS_TOPIC_ARN="arn:aws:sns:us-east-1:123456789012:ops")

    app._alert_operators(cfg, "subject", "body")  # must not raise


def test_oom_incident_reaches_the_model(event, monkeypatch, evidence, report):
    """Simulated incident #2: tasks OOM-killed, no deployment involved."""
    evidence["ecs"].update(
        {
            "recent_deployment_minutes_ago": 2880.0,
            "deployment_in_progress": False,
            "failure_classes": ["container_oom"],
            "stopped_task_count": 7,
        }
    )
    evidence["cloudwatch"]["metrics"]["memory_utilization_max_pct"]["max"] = 98.0
    evidence["cloudwatch"]["derived"] = {"memory_saturated": True, "cpu_saturated": False}
    evidence["splunk"]["dominant_pattern"] = "oom_killed"

    seen: dict = {}

    def fake_analyze(cfg, alert, ev):
        seen["evidence"] = ev
        return report

    monkeypatch.setattr(app, "collect_evidence", lambda cfg, alert: evidence)
    monkeypatch.setattr(bedrock, "analyze", fake_analyze)
    monkeypatch.setattr(slack, "post", lambda *_a: {"delivered": True})

    app.handler(event, None)

    assert seen["evidence"]["ecs"]["failure_classes"] == ["container_oom"]
    assert seen["evidence"]["cloudwatch"]["derived"]["memory_saturated"] is True
