from __future__ import annotations

import json

import pytest

from utils import config as config_module


class FakeSecretsClient:
    def __init__(self, payload):
        self.payload = payload
        self.requested: list[str] = []

    # botocore uses PascalCase keyword arguments.
    def get_secret_value(self, SecretId):
        self.requested.append(SecretId)
        return {"SecretString": self.payload}


@pytest.fixture
def patch_boto(monkeypatch):
    def _patch(payload):
        client = FakeSecretsClient(payload)
        monkeypatch.setattr(config_module.boto3, "client", lambda *_a, **_kw: client)
        config_module.reset_caches()
        return client

    return _patch


def test_config_reads_the_environment(cfg):
    assert cfg.ecs_cluster == "prod-ecs"
    assert cfg.lookback_minutes == 15
    assert cfg.lookback_seconds == 900
    assert cfg.bedrock_model_id == "anthropic.claude-opus-5"
    assert cfg.splunk_verify_tls is True
    assert cfg.dry_run is False


def test_config_is_cached_across_calls():
    assert config_module.load_config() is config_module.load_config()


def test_invalid_integer_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("LOOKBACK_MINUTES", "not-a-number")
    config_module.reset_caches()

    assert config_module.load_config().lookback_minutes == 15


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("YES", True), ("on", True), ("false", False), ("", False)],
)
def test_boolean_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("DRY_RUN", raw)
    config_module.reset_caches()

    assert config_module.load_config().dry_run is expected


def test_secrets_are_loaded_and_stringified(patch_boto):
    client = patch_boto(json.dumps({"splunk_token": "abc", "port": 8089, "empty": None}))

    secrets = config_module.load_secrets()

    assert secrets["splunk_token"] == "abc"
    assert secrets["port"] == "8089"
    assert "empty" not in secrets  # null values are dropped, not stringified
    assert client.requested == [config_module.load_config().secret_arn]


def test_secrets_are_fetched_once(patch_boto):
    client = patch_boto(json.dumps({"splunk_token": "abc"}))

    config_module.load_secrets()
    config_module.load_secrets()

    assert len(client.requested) == 1


def test_malformed_secret_json_degrades_to_empty(patch_boto):
    patch_boto("not json at all")

    assert config_module.load_secrets() == {}


def test_non_object_secret_degrades_to_empty(patch_boto):
    patch_boto(json.dumps(["a", "list"]))

    assert config_module.load_secrets() == {}


def test_missing_secret_arn_skips_the_api_call(monkeypatch):
    monkeypatch.setenv("SECRET_ARN", "")

    def explode(*_args, **_kwargs):
        raise AssertionError("boto3 must not be called without SECRET_ARN")

    monkeypatch.setattr(config_module.boto3, "client", explode)
    config_module.reset_caches()

    assert config_module.load_secrets() == {}
