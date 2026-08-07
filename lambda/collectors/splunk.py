"""Splunk evidence with client-side reduction.

Rather than shipping raw logs to the model, this collector runs one targeted
search, classifies each event into a known error pattern, ranks the patterns by
volume, and keeps a couple of exemplars per pattern. That keeps the prompt small
and the diagnosis focused on signal rather than log volume.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import httpx

from collectors import collector
from utils.config import Config, load_secrets
from utils.logger import get_logger
from utils.parser import Alert

log = get_logger(__name__)

SEARCH_TIMEOUT_SECONDS = 45
EXEMPLARS_PER_PATTERN = 2
MAX_EXEMPLAR_CHARS = 400

# Ordered: the first pattern that matches wins, so put specific causes above
# generic ones (an OOMKilled line also contains "error" in many log formats).
ERROR_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "oom_killed",
        r"OOMKilled|OutOfMemoryError|java\.lang\.OutOfMemoryError|Cannot allocate memory",
        "critical",
    ),
    ("connection_refused", r"[Cc]onnection refused|ECONNREFUSED", "high"),
    (
        "connection_pool_exhausted",
        r"connection pool|pool (is )?exhausted|too many connections|"
        r"[Cc]onnection is not available|HikariPool|acquire timeout",
        "critical",
    ),
    (
        "database_error",
        r"SQLException|DatabaseError|deadlock|could not obtain connection|psycopg2|OperationalError",
        "high",
    ),
    ("timeout", r"[Tt]imed? ?out|TimeoutException|ETIMEDOUT|context deadline exceeded|read timeout", "high"),
    (
        "null_pointer",
        r"NullPointerException|TypeError: .*of (null|undefined)|AttributeError: 'NoneType'",
        "high",
    ),
    ("http_500", r"\b5\d{2}\b.*(status|response|code)|HTTP/1\.[01]\" 5\d{2}|status[\"']?[: ]+5\d{2}", "high"),
    ("auth_failure", r"401 Unauthorized|403 Forbidden|AccessDenied|InvalidCredentials", "medium"),
    ("dns_failure", r"UnknownHostException|EAI_AGAIN|no such host|Name or service not known", "high"),
    ("circuit_breaker", r"[Cc]ircuit ?breaker (open|tripped)|CallNotPermittedException", "high"),
    ("rate_limited", r"429 Too Many Requests|RateLimitExceeded|ThrottlingException", "medium"),
    ("unhandled_exception", r"Exception|Traceback \(most recent call last\)|panic:|FATAL", "high"),
    ("generic_error", r"\bERROR\b|\blevel=error\b|\bERR\b", "medium"),
)

_COMPILED = tuple((name, re.compile(pattern), severity) for name, pattern, severity in ERROR_PATTERNS)

# Splunk search terms, kept broad — precision comes from client-side classification.
_SEARCH_TERMS = (
    "ERROR OR Exception OR Timeout OR OOMKilled OR "
    '"Connection refused" OR NullPointerException OR FATAL OR "500"'
)


def _spl(cfg: Config, alert: Alert) -> str:
    index = cfg.splunk_index.replace('"', "")
    service = alert.service.replace('"', "")
    return (
        f'search index="{index}" service="{service}" ({_SEARCH_TERMS}) '
        f"| head {cfg.splunk_max_events} "
        "| table _time, host, source, sourcetype, _raw"
    )


def _splunk_time(moment: datetime) -> str:
    # Splunk accepts epoch seconds for earliest/latest, which sidesteps timezone
    # ambiguity in the server's local-time parsing.
    return str(int(moment.timestamp()))


def _iter_events(text: str) -> Iterable[dict[str, Any]]:
    """Parse the NDJSON stream returned by /services/search/jobs/export."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            yield result


def _classify(raw: str) -> tuple[str, str]:
    for name, pattern, severity in _COMPILED:
        if pattern.search(raw):
            return name, severity
    return "uncategorized", "low"


def _truncate(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_EXEMPLAR_CHARS:
        return collapsed
    return collapsed[:MAX_EXEMPLAR_CHARS] + "…"


def _reduce(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse raw events into ranked patterns with exemplars."""
    counts: Counter[str] = Counter()
    severities: dict[str, str] = {}
    exemplars: dict[str, list[dict[str, str]]] = defaultdict(list)
    hosts: dict[str, Counter[str]] = defaultdict(Counter)
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}

    for event in events:
        raw = str(event.get("_raw", ""))
        if not raw:
            continue
        name, severity = _classify(raw)
        counts[name] += 1
        severities[name] = severity

        timestamp = str(event.get("_time", ""))
        if timestamp:
            if name not in first_seen or timestamp < first_seen[name]:
                first_seen[name] = timestamp
            if name not in last_seen or timestamp > last_seen[name]:
                last_seen[name] = timestamp

        host = str(event.get("host", ""))
        if host:
            hosts[name][host] += 1

        if len(exemplars[name]) < EXEMPLARS_PER_PATTERN:
            exemplars[name].append({"time": timestamp, "host": host, "message": _truncate(raw)})

    ranked = []
    for name, count in counts.most_common():
        ranked.append(
            {
                "pattern": name,
                "severity": severities[name],
                "count": count,
                "share_pct": round(100 * count / max(len(events), 1), 1),
                "first_seen": first_seen.get(name),
                "last_seen": last_seen.get(name),
                "distinct_hosts": len(hosts[name]),
                "top_hosts": [h for h, _ in hosts[name].most_common(3)],
                "examples": exemplars[name],
            }
        )

    return {
        "total_events_scanned": len(events),
        "patterns": ranked,
        "dominant_pattern": ranked[0]["pattern"] if ranked else None,
    }


@collector("splunk")
def collect(cfg: Config, alert: Alert) -> dict[str, Any]:
    """Run one targeted search and return ranked error evidence."""
    if not cfg.splunk_host:
        return {"available": False, "error": "SPLUNK_HOST not configured"}

    token = load_secrets().get("splunk_token")
    if not token:
        return {"available": False, "error": "splunk_token missing from secret"}

    start, end = alert.window(cfg.lookback_minutes)
    query = _spl(cfg, alert)

    response = httpx.post(
        f"{cfg.splunk_host}/services/search/jobs/export",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "search": query,
            "output_mode": "json",
            "earliest_time": _splunk_time(start),
            "latest_time": _splunk_time(end),
            "exec_mode": "oneshot",
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
        verify=cfg.splunk_verify_tls,
    )
    response.raise_for_status()

    events = list(_iter_events(response.text))
    log.info("splunk search complete", extra={"event_count": len(events)})

    reduced = _reduce(events)
    reduced.update(
        {
            "query": query,
            "index": cfg.splunk_index,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "truncated": len(events) >= cfg.splunk_max_events,
        }
    )
    return reduced
