from __future__ import annotations

import httpx
import respx

from collectors import grafana

ANNOTATIONS_URL = "https://grafana.example.com/api/annotations"


@respx.mock
def test_collects_annotations_and_flags_deploy_markers(cfg, alert, secrets):
    respx.get(ANNOTATIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"time": 1770000000000, "text": "deployed payment-api:42", "tags": ["deploy", "prod"]},
                {"time": 1770000060000, "text": "manual note", "tags": ["ops"]},
                {"time": 1770000120000, "text": "alert fired", "tags": [], "alertId": 7},
            ],
        )
    )

    result = grafana.collect(cfg, alert)

    assert result["available"] is True
    assert result["annotation_count"] == 3
    assert len(result["deploy_markers"]) == 1
    assert result["deploy_markers"][0]["text"] == "deployed payment-api:42"
    assert result["annotations"][2]["type"] == "alert"
    assert result["dashboard_url"] == alert.dashboard_url


@respx.mock
def test_release_tag_also_counts_as_a_deploy_marker(cfg, alert, secrets):
    respx.get(ANNOTATIONS_URL).mock(
        return_value=httpx.Response(200, json=[{"time": 1, "text": "v42", "tags": ["Release"]}])
    )

    assert len(grafana.collect(cfg, alert)["deploy_markers"]) == 1


@respx.mock
def test_window_and_auth_are_sent(cfg, alert, secrets):
    route = respx.get(ANNOTATIONS_URL).mock(return_value=httpx.Response(200, json=[]))

    grafana.collect(cfg, alert)

    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer grafana-test-token"

    start, _ = alert.window(cfg.lookback_minutes)
    assert request.url.params["from"] == str(int(start.timestamp() * 1000))
    assert request.url.params["limit"] == str(grafana.MAX_ANNOTATIONS)


@respx.mock
def test_unexpected_payload_shape_degrades(cfg, alert, secrets):
    respx.get(ANNOTATIONS_URL).mock(return_value=httpx.Response(200, json={"error": "nope"}))

    result = grafana.collect(cfg, alert)

    assert result["available"] is False
    assert "unexpected" in result["error"]


@respx.mock
def test_http_error_degrades(cfg, alert, secrets):
    respx.get(ANNOTATIONS_URL).mock(return_value=httpx.Response(401, text="unauthorized"))

    result = grafana.collect(cfg, alert)

    assert result["available"] is False
    assert "401" in result["error"]


def test_missing_url_disables_the_collector(alert, monkeypatch, secrets):
    monkeypatch.setenv("GRAFANA_URL", "")
    from utils import config as config_module

    config_module.reset_caches()

    result = grafana.collect(config_module.load_config(), alert)

    assert result["available"] is False
    assert "GRAFANA_URL" in result["error"]


def test_missing_token_disables_the_collector(cfg, alert, monkeypatch):
    monkeypatch.setattr(grafana, "load_secrets", dict)

    result = grafana.collect(cfg, alert)

    assert result["available"] is False
    assert "grafana_token" in result["error"]
