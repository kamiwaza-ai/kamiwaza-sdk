"""Content-free diagnostics for the federation validation SSE readers.

Observe the existing parser without changing its records, assertions, or errors.
Only fixed labels and counters are logged: never URLs, tokens, event payloads,
unknown event names, job IDs, or arbitrary server error text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from typing import Any

_LOGGER = logging.getLogger(__name__)
_EVENTS = ("chunk", "complete", "error", "done", "message", "other")
_STATUSES = frozenset({"PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELED"})
_TRANSPORTS = frozenset({"sse", "inline", "grpc"})


def diagnostic_lines(response: Any) -> Iterator[str]:
    """Yield original lines while recording bounded, content-free stream shape.

    Event counters count headers, not completed SSE events. ``eof`` means the
    line iterator exhausted normally, not that a completion event was received.
    """
    events = dict.fromkeys(_EVENTS, 0)
    summary: dict[str, Any] = {
        "http_status": getattr(response, "status_code", None),
        "content_type": _content_type(response),
        "redirects": len(getattr(response, "history", [])),
        "events": events,
        "lines": 0,
        "data_lines": 0,
        "blank_lines": 0,
        "eof": False,
    }
    try:
        for raw in response.iter_lines(decode_unicode=True):
            summary["lines"] += 1
            _observe_line(raw, summary, events)
            yield raw
        summary["eof"] = True
    finally:
        _LOGGER.info(
            "federation_retrieval_stream %s", json.dumps(summary, sort_keys=True)
        )


def _content_type(response: Any) -> str:
    headers = getattr(response, "headers", {})
    media_type = headers.get("Content-Type", headers.get("content-type", ""))
    media_type = media_type.split(";", 1)[0].strip().lower()
    return "sse" if media_type == "text/event-stream" else "other"


def _observe_line(raw: str, summary: dict, events: dict[str, int]) -> None:
    if not raw:
        summary["blank_lines"] += 1
    elif raw.startswith("data:"):
        summary["data_lines"] += 1
    elif raw.startswith("event:"):
        event = raw[6:].strip()
        events[event if event in _EVENTS else "other"] += 1


def log_missing_audit_job_state(
    client: Any, path: str, headers: Mapping[str, str], audits: list
) -> None:
    """Best-effort read after missing audit evidence; never replace its failure."""
    if audits:
        return
    try:
        job = client._request("GET", path, headers=dict(headers), timeout=10)
    except Exception:
        # The original known-answer assertion still fails. This separate lookup
        # is diagnostic only, and arbitrary exception strings can contain secrets.
        summary = {"lookup": "failed"}
    else:
        summary = {
            "lookup": "ok",
            "status": _known_field(job, "status", _STATUSES),
            "transport": _known_field(job, "transport", _TRANSPORTS),
        }
    _LOGGER.warning("federation_retrieval_job %s", json.dumps(summary, sort_keys=True))


def _known_field(job: Any, name: str, choices: frozenset[str]) -> str:
    value = job.get(name) if isinstance(job, Mapping) else getattr(job, name, None)
    return value if isinstance(value, str) and value in choices else "unknown"
