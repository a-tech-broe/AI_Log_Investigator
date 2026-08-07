"""Evidence collectors.

Every collector returns a plain dict shaped like::

    {"available": bool, "error": str | None, ...collector-specific fields}

A collector never raises into the orchestrator: a failing data source degrades
the report, it does not abort the investigation.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)


def collector(name: str) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Wrap a collector so failures become `available: False` instead of exceptions."""

    def decorate(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                log.exception("collector failed", extra={"collector": name})
                return {
                    "available": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "source": name,
                }
            result.setdefault("available", True)
            result.setdefault("error", None)
            result.setdefault("source", name)
            return result

        return wrapper

    return decorate
