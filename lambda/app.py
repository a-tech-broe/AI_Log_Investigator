"""Lambda entry point: orchestrate alert → evidence → analysis → Slack."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import boto3

from ai import bedrock
from collectors import cloudwatch as cloudwatch_collector
from collectors import ecs as ecs_collector
from collectors import grafana as grafana_collector
from collectors import splunk as splunk_collector
from notifications import slack
from utils.config import BOTO_CONFIG, Config, load_config
from utils.logger import (
    clear_context,
    configure_logging,
    get_logger,
    set_alert_context,
    set_correlation_id,
)
from utils.parser import Alert, AlertParseError, parse_alert

configure_logging()
log = get_logger(__name__)

COLLECTORS: dict[str, Callable[[Config, Alert], dict[str, Any]]] = {
    "ecs": ecs_collector.collect,
    "cloudwatch": cloudwatch_collector.collect,
    "splunk": splunk_collector.collect,
    "grafana": grafana_collector.collect,
}


def collect_evidence(cfg: Config, alert: Alert) -> dict[str, Any]:
    """Run every collector concurrently — they hit independent, I/O-bound APIs."""
    started = time.monotonic()
    evidence: dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=len(COLLECTORS)) as pool:
        futures = {name: pool.submit(fn, cfg, alert) for name, fn in COLLECTORS.items()}
        for name, future in futures.items():
            # Each collector is already wrapped by @collector, so it returns a
            # dict rather than raising — but guard the executor boundary anyway.
            try:
                evidence[name] = future.result()
            except Exception as exc:
                log.exception("collector future failed", extra={"collector": name})
                evidence[name] = {"available": False, "error": str(exc), "source": name}

    elapsed = round(time.monotonic() - started, 2)
    available = [name for name, data in evidence.items() if data.get("available")]
    log.info(
        "evidence collection complete",
        extra={"elapsed_seconds": elapsed, "available_sources": available},
    )
    evidence["_meta"] = {"elapsed_seconds": elapsed, "available_sources": available}
    return evidence


def _persist(cfg: Config, alert: Alert, evidence: dict[str, Any], report: dict[str, Any]) -> str | None:
    """Archive the investigation to S3 for postmortems and prompt iteration."""
    if not cfg.evidence_bucket or cfg.dry_run:
        return None

    now = datetime.now(UTC)
    key = f"investigations/{now:%Y/%m/%d}/{alert.service}/{now:%H%M%S}-{uuid.uuid4().hex[:8]}.json"
    payload = {
        "alert": alert.to_dict(),
        "evidence": evidence,
        "report": report,
        "generated_at": now.isoformat(),
    }

    try:
        boto3.client("s3", region_name=cfg.aws_region, config=BOTO_CONFIG).put_object(
            Bucket=cfg.evidence_bucket,
            Key=key,
            Body=json.dumps(payload, default=str).encode(),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        log.info("archived investigation", extra={"s3_key": key})
        return key
    except Exception:
        log.exception("failed to archive investigation")
        return None


def _alert_operators(cfg: Config, subject: str, message: str) -> None:
    """Publish to SNS when the investigator itself fails."""
    if not cfg.sns_topic_arn or cfg.dry_run:
        return
    try:
        boto3.client("sns", region_name=cfg.aws_region, config=BOTO_CONFIG).publish(
            TopicArn=cfg.sns_topic_arn,
            Subject=subject[:100],
            Message=message,
        )
    except Exception:
        log.exception("failed to publish SNS failure notification")


def investigate(cfg: Config, alert: Alert) -> dict[str, Any]:
    """Run one full investigation and return the handler response body."""
    evidence = collect_evidence(cfg, alert)

    try:
        report = bedrock.analyze(cfg, alert, evidence)
    except Exception as exc:
        log.exception("bedrock analysis failed; falling back to raw evidence")
        report = bedrock.fallback_report(alert, evidence, f"{type(exc).__name__}: {exc}")
        _alert_operators(
            cfg,
            f"AI Log Investigator: analysis failed for {alert.service}",
            f"Alert: {alert.name}\nService: {alert.service}\nError: {exc}",
        )

    delivery = slack.post(cfg, alert, report, evidence)
    s3_key = _persist(cfg, alert, evidence, report)

    return {
        "service": alert.service,
        "alert": alert.name,
        "root_cause": report.get("root_cause"),
        "confidence": report.get("confidence"),
        "severity": report.get("severity"),
        "category": report.get("category"),
        "sources": evidence.get("_meta", {}).get("available_sources", []),
        "slack": delivery,
        "evidence_key": s3_key,
        "degraded": bool(report.get("_meta", {}).get("degraded")),
    }


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """EventBridge entry point."""
    clear_context()
    request_id = getattr(context, "aws_request_id", None) or uuid.uuid4().hex
    set_correlation_id(request_id)

    cfg = load_config()
    configure_logging(cfg.log_level)

    try:
        alert = parse_alert(event)
    except AlertParseError as exc:
        # A malformed alert is a permanent failure: retrying cannot fix it, so
        # return 400 rather than raising into EventBridge's retry policy.
        log.error("could not parse alert", extra={"parse_error": str(exc)})
        return {"statusCode": 400, "body": {"error": str(exc)}}

    set_alert_context(
        service=alert.service,
        alert_name=alert.name,
        severity=alert.severity,
        cluster=alert.cluster or cfg.ecs_cluster,
    )

    if not alert.is_firing:
        log.info("alert is resolved; skipping investigation", extra={"status": alert.status})
        return {"statusCode": 200, "body": {"skipped": "alert_resolved", "service": alert.service}}

    log.info("starting investigation")

    try:
        body = investigate(cfg, alert)
    except Exception as exc:
        log.exception("investigation failed")
        _alert_operators(
            cfg,
            f"AI Log Investigator: investigation failed for {alert.service}",
            f"Alert: {alert.name}\nError: {exc}",
        )
        # Re-raise so EventBridge retries and the failure lands in the DLQ.
        raise

    log.info("investigation complete", extra={"confidence": body.get("confidence")})
    return {"statusCode": 200, "body": body}


if __name__ == "__main__":  # pragma: no cover - local smoke test
    sample_path = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "grafana_alert.json")
    with open(sample_path, encoding="utf-8") as fh:
        print(json.dumps(handler(json.load(fh)), indent=2, default=str))
