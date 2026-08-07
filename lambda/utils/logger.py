"""Structured JSON logging for the AI Log Investigator Lambda."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
# Default is None rather than {} so the sentinel can never be mutated in place
# and leak alert fields from one invocation into the next.
_alert_context: ContextVar[dict[str, Any] | None] = ContextVar("alert_context", default=None)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "asctime",
    "message",
}


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line so CloudWatch Insights can query fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": _correlation_id.get(),
        }

        alert = _alert_context.get()
        if alert:
            payload["alert"] = alert

        # Anything passed via logger.info("...", extra={...}) lands on the record.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Install the JSON formatter on the root logger.

    Lambda pre-configures a root handler, so replace its formatter rather than
    adding a second handler (which would duplicate every line).
    """
    resolved = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    root.setLevel(resolved)

    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))

    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())

    # Third-party chatter is rarely useful at INFO in an incident path.
    for noisy in ("botocore", "boto3", "urllib3", "httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_alert_context(**fields: Any) -> None:
    """Attach alert identifiers to every subsequent log line in this invocation."""
    _alert_context.set({k: v for k, v in fields.items() if v is not None})


def clear_context() -> None:
    _correlation_id.set("-")
    _alert_context.set(None)
