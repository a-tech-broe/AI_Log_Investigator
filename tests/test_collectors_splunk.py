from __future__ import annotations

import dataclasses
import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from collectors import splunk

EXPORT_URL = "https://splunk.example.com:8089/services/search/jobs/export"


def ndjson(*raws: str) -> str:
    lines = []
    for i, raw in enumerate(raws):
        lines.append(
            json.dumps(
                {
                    "result": {
                        "_time": f"2026-08-07T10:3{i % 10}:00.000Z",
                        "host": f"ip-10-0-1-{i % 3}",
                        "source": "/var/log/app.log",
                        "_raw": raw,
                    }
                }
            )
        )
    return "\n".join(lines)


@respx.mock
def test_reduces_events_into_ranked_patterns(cfg, alert, secrets):
    respx.post(EXPORT_URL).mock(
        return_value=httpx.Response(
            200,
            text=ndjson(
                "HikariPool-1 - Connection is not available, request timed out after 30000ms",
                "HikariPool-1 - Connection is not available, request timed out after 30000ms",
                "java.lang.NullPointerException at com.acme.Checkout",
                "INFO request completed",
            ),
        )
    )

    result = splunk.collect(cfg, alert)

    assert result["available"] is True
    assert result["total_events_scanned"] == 4
    assert result["dominant_pattern"] == "connection_pool_exhausted"

    top = result["patterns"][0]
    assert top["count"] == 2
    assert top["severity"] == "critical"
    assert top["share_pct"] == 50.0
    assert len(top["examples"]) <= splunk.EXEMPLARS_PER_PATTERN
    assert top["distinct_hosts"] >= 1

    patterns = {p["pattern"] for p in result["patterns"]}
    assert "null_pointer" in patterns
    assert "uncategorized" in patterns


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Container killed: OOMKilled", "oom_killed"),
        ("dial tcp 10.0.0.1:5432: connection refused", "connection_refused"),
        ("HikariPool-1 pool is exhausted", "connection_pool_exhausted"),
        ("org.postgresql.util.PSQLException deadlock detected", "database_error"),
        ("context deadline exceeded", "timeout"),
        ("AttributeError: 'NoneType' object has no attribute 'id'", "null_pointer"),
        ("UnknownHostException: db.internal", "dns_failure"),
        ("CallNotPermittedException: circuit breaker open", "circuit_breaker"),
        ("429 Too Many Requests", "rate_limited"),
        ('level=error msg="handler failed"', "generic_error"),
        ("everything is fine", "uncategorized"),
    ],
)
def test_pattern_classification(line, expected):
    assert splunk._classify(line)[0] == expected


def test_specific_patterns_win_over_generic():
    # An OOM line also contains "ERROR"-ish words; ordering must favor the cause.
    assert splunk._classify("ERROR OOMKilled container terminated")[0] == "oom_killed"


@respx.mock
def test_query_scopes_index_service_and_window(cfg, alert, secrets):
    route = respx.post(EXPORT_URL).mock(return_value=httpx.Response(200, text=""))

    result = splunk.collect(cfg, alert)

    sent = parse_qs(route.calls[0].request.content.decode())
    assert 'index="prod"' in sent["search"][0]
    assert 'service="payment-api"' in sent["search"][0]
    assert f"head {cfg.splunk_max_events}" in result["query"]

    start, _ = alert.window(cfg.lookback_minutes)
    assert sent["earliest_time"][0] == str(int(start.timestamp()))
    assert int(sent["latest_time"][0]) >= int(alert.started_at.timestamp())


@respx.mock
def test_bearer_token_is_sent(cfg, alert, secrets):
    route = respx.post(EXPORT_URL).mock(return_value=httpx.Response(200, text=""))

    splunk.collect(cfg, alert)

    assert route.calls[0].request.headers["Authorization"] == "Bearer splunk-test-token"


@respx.mock
def test_truncation_flag_when_head_limit_reached(cfg, alert, secrets):
    capped = dataclasses.replace(cfg, splunk_max_events=2)
    respx.post(EXPORT_URL).mock(return_value=httpx.Response(200, text=ndjson("ERROR a", "ERROR b")))

    assert splunk.collect(capped, alert)["truncated"] is True


@respx.mock
def test_http_error_degrades(cfg, alert, secrets):
    respx.post(EXPORT_URL).mock(return_value=httpx.Response(503, text="unavailable"))

    result = splunk.collect(cfg, alert)

    assert result["available"] is False
    assert "503" in result["error"]


@respx.mock
def test_malformed_lines_are_skipped(cfg, alert, secrets):
    respx.post(EXPORT_URL).mock(
        return_value=httpx.Response(200, text="not json\n" + ndjson("ERROR real event") + "\n{}")
    )

    assert splunk.collect(cfg, alert)["total_events_scanned"] == 1


def test_missing_token_is_reported(cfg, alert, monkeypatch):
    monkeypatch.setattr(splunk, "load_secrets", dict)

    result = splunk.collect(cfg, alert)

    assert result["available"] is False
    assert "splunk_token" in result["error"]


def test_missing_host_is_reported(alert, monkeypatch, secrets):
    monkeypatch.setenv("SPLUNK_HOST", "")
    from utils import config as config_module

    config_module.reset_caches()

    result = splunk.collect(config_module.load_config(), alert)

    assert result["available"] is False
    assert "SPLUNK_HOST" in result["error"]


def test_long_messages_are_truncated():
    long_line = "x" * (splunk.MAX_EXEMPLAR_CHARS + 500)
    assert len(splunk._truncate(long_line)) == splunk.MAX_EXEMPLAR_CHARS + 1  # includes ellipsis
