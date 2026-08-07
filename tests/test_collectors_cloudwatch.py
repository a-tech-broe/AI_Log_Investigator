from __future__ import annotations

import pytest

from collectors import cloudwatch


class FakeCloudWatchClient:
    def __init__(self, series: dict[str, list[float]], messages=None):
        self.series = series
        self.messages = messages or []
        self.last_request: dict = {}

    def get_metric_data(self, **kwargs):
        self.last_request = kwargs
        results = []
        for query in kwargs["MetricDataQueries"]:
            values = self.series.get(query["Id"], [])
            results.append({"Id": query["Id"], "Values": values, "Timestamps": []})
        return {"MetricDataResults": results, "Messages": self.messages}


@pytest.fixture
def patch_client(monkeypatch):
    def _patch(client):
        monkeypatch.setattr(cloudwatch, "_client", lambda _cfg: client)
        return client

    return _patch


def test_summarizes_each_series(cfg, alert, patch_client):
    patch_client(
        FakeCloudWatchClient(
            {
                "cpu": [30.0, 25.0, 20.0],
                "cpu_max": [31.0, 28.0],
                "mem": [40.0, 42.0],
                "mem_max": [44.0],
                "alb_5xx": [40.0, 60.0, 20.0],
                "alb_req": [500.0, 600.0, 400.0],
            }
        )
    )

    result = cloudwatch.collect(cfg, alert)

    assert result["available"] is True
    cpu = result["metrics"]["cpu_utilization_avg_pct"]
    assert cpu["latest"] == 30.0  # newest-first scan order
    assert cpu["max"] == 30.0
    assert cpu["avg"] == 25.0
    assert cpu["datapoints"] == 3
    assert result["metrics"]["alb_5xx_count"]["total"] == 120.0


def test_derives_error_rate_and_saturation(cfg, alert, patch_client):
    patch_client(
        FakeCloudWatchClient(
            {
                "cpu_max": [92.0],
                "mem_max": [50.0],
                "alb_5xx": [50.0, 50.0],
                "alb_req": [500.0, 500.0],
                "alb_unhealthy": [2.0],
            }
        )
    )

    derived = cloudwatch.collect(cfg, alert)["derived"]

    assert derived["error_rate_pct"] == 10.0
    assert derived["cpu_saturated"] is True
    assert derived["memory_saturated"] is False
    assert derived["resource_pressure"] is True
    assert derived["has_unhealthy_targets"] is True


def test_no_error_rate_without_request_volume(cfg, alert, patch_client):
    patch_client(FakeCloudWatchClient({"alb_5xx": [10.0], "alb_req": [0.0]}))

    assert "error_rate_pct" not in cloudwatch.collect(cfg, alert)["derived"]


def test_empty_series_are_reported_as_missing(cfg, alert, patch_client):
    patch_client(FakeCloudWatchClient({"cpu": [10.0]}))

    result = cloudwatch.collect(cfg, alert)

    assert "cpu_utilization_avg_pct" in result["metrics"]
    assert "memory_utilization_avg_pct" in result["missing_metrics"]


def test_alb_queries_omitted_without_arn_suffixes(alert, monkeypatch, patch_client):
    monkeypatch.setenv("ALB_ARN_SUFFIX", "")
    monkeypatch.setenv("TARGET_GROUP_ARN_SUFFIX", "")
    from utils import config as config_module

    config_module.reset_caches()
    client = patch_client(FakeCloudWatchClient({}))

    cloudwatch.collect(config_module.load_config(), alert)

    ids = {q["Id"] for q in client.last_request["MetricDataQueries"]}
    assert not any(i.startswith("alb_") for i in ids)
    assert "cpu" in ids


def test_window_uses_alert_lookback(cfg, alert, patch_client):
    client = patch_client(FakeCloudWatchClient({}))

    cloudwatch.collect(cfg, alert)

    start, _ = alert.window(cfg.lookback_minutes)
    assert client.last_request["StartTime"] == start
    # `end` is anchored on "now", so compare loosely rather than exactly.
    assert client.last_request["EndTime"] >= alert.started_at
    assert client.last_request["ScanBy"] == "TimestampDescending"


def test_api_failure_degrades(cfg, alert, monkeypatch):
    class Boom:
        def get_metric_data(self, **_kwargs):
            raise RuntimeError("throttled")

    monkeypatch.setattr(cloudwatch, "_client", lambda _cfg: Boom())

    result = cloudwatch.collect(cfg, alert)

    assert result["available"] is False
    assert "throttled" in result["error"]
