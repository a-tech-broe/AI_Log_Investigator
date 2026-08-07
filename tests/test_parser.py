from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from utils.parser import Alert, AlertParseError, parse_alert


def test_parses_grafana_eventbridge_payload(event):
    alert = parse_alert(event)

    assert alert.service == "payment-api"
    assert alert.name == "HighErrorRate"
    assert alert.severity == "critical"
    assert alert.cluster == "prod-ecs"
    assert alert.is_firing
    assert alert.started_at == datetime(2026, 8, 7, 10, 35, tzinfo=UTC)
    assert "HTTP 500 rate" in alert.summary
    assert alert.dashboard_url.endswith("/payment-api")


def test_common_labels_merge_with_alert_labels(event):
    alert = parse_alert(event)
    assert alert.labels["environment"] == "prod"  # from commonLabels
    assert alert.labels["alertname"] == "HighErrorRate"  # from alert labels


def test_prefers_firing_alert_over_resolved(event):
    resolved = dict(event["detail"]["alerts"][0], status="resolved")
    firing = dict(
        event["detail"]["alerts"][0],
        status="firing",
        labels={**event["detail"]["alerts"][0]["labels"], "service": "checkout-api"},
    )
    event["detail"]["alerts"] = [resolved, firing]

    assert parse_alert(event).service == "checkout-api"


def test_accepts_bare_detail_without_alerts_array():
    alert = parse_alert(
        {
            "detail": {
                "status": "firing",
                "commonLabels": {"service": "cart-api", "alertname": "LatencyHigh", "severity": "p2"},
            }
        }
    )
    assert alert.service == "cart-api"
    assert alert.severity == "high"


def test_accepts_api_destination_body_wrapper():
    alert = parse_alert(
        {
            "detail": {
                "body": {"alerts": [{"status": "firing", "labels": {"service": "orders", "alertname": "A"}}]}
            }
        }
    )
    assert alert.service == "orders"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("P1", "critical"), ("sev2", "high"), ("warn", "warning"), ("Info", "info"), ("", "unknown")],
)
def test_severity_normalization(raw, expected):
    alert = parse_alert({"detail": {"alerts": [{"labels": {"service": "s", "severity": raw}}]}})
    assert alert.severity == expected


@pytest.mark.parametrize("label", ["service", "service_name", "app", "ecs_service", "job"])
def test_service_label_aliases(label):
    alert = parse_alert({"detail": {"alerts": [{"labels": {label: "billing-api"}}]}})
    assert alert.service == "billing-api"


def test_missing_service_is_a_parse_error():
    with pytest.raises(AlertParseError, match="no service label"):
        parse_alert({"detail": {"alerts": [{"labels": {"alertname": "Orphan"}}]}})


def test_non_mapping_event_rejected():
    with pytest.raises(AlertParseError):
        parse_alert(["not", "a", "dict"])  # type: ignore[arg-type]


def test_nanosecond_timestamp_is_truncated_not_dropped():
    alert = parse_alert(
        {"detail": {"alerts": [{"labels": {"service": "s"}, "startsAt": "2026-08-07T10:35:00.123456789Z"}]}}
    )
    assert alert.started_at.year == 2026
    assert alert.started_at.microsecond == 123456


def test_zero_timestamp_falls_back_to_now():
    alert = parse_alert(
        {"detail": {"alerts": [{"labels": {"service": "s"}, "startsAt": "0001-01-01T00:00:00Z"}]}}
    )
    assert alert.started_at > datetime.now(UTC) - timedelta(seconds=5)


def test_window_anchors_on_alert_start_not_now():
    started = datetime.now(UTC) - timedelta(minutes=30)
    alert = Alert(name="A", service="s", severity="high", status="firing", started_at=started)

    start, end = alert.window(15)

    assert start == started - timedelta(minutes=15)
    assert end >= datetime.now(UTC) - timedelta(seconds=5)


def test_to_dict_is_json_safe(alert):
    payload = alert.to_dict()
    assert isinstance(payload["started_at"], str)
    assert payload["service"] == "payment-api"
