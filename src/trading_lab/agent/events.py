"""
Structured event log — the agent-harness contract.

Every significant occurrence in the system writes one jsonl line to
`logs/events.jsonl`. An external operator agent (cron'd on the deployment
machine) tails this file, diffs against its last read offset, and decides
what to forward via SMS/email/Slack/wherever.

Schema (one line per event):

    {
      "ts":       "2026-05-25T19:30:00.123456+00:00",   // UTC ISO-8601
      "type":     "<event type — enum>",
      "severity": "info|warn|critical",
      "slug":     "<hypothesis slug or null>",
      "summary":  "human-readable one-liner",
      "data":     { ... event-specific payload ... }
    }

Event types (current — extend as needed):

  - lifecycle_transition  — slug moved state. severity=info; data has
                             {from_state, to_state, actor, reason}
  - watcher_halt          — paper_watcher halted a strategy. severity=warn
  - watcher_retire        — paper_watcher retired a strategy. severity=critical
  - kill_switch_tripped   — global kill switch flag written. severity=critical
  - kill_switch_cleared   — global kill switch cleared. severity=info
  - paper_signal          — strategy emitted an order intent. severity=info
                             (high-frequency — operator agent should
                             aggregate/throttle, not forward each one)
  - paper_summary         — periodic summariser ran. severity=info
                             data has {date, realised_pnl_usdc, n_pairs, ...}
  - eval_decision         — eval_strategy.py applied a decision rule.
                             severity = warn if rejected else info
  - optimize_decision     — optimize_strategy.py applied a decision rule.
  - discovery             — new hypothesis registered. severity=info

Design notes:
  - Append-only. Never rotate or truncate inside the process (operator
    rotates manually or via logrotate).
  - Each line is independently valid JSON — partial lines are an operator
    error, not the writer's responsibility.
  - `severity` is the FILTER an operator agent uses. `critical` events
    should always forward; `warn` aggregates per day; `info` is
    inspection-only unless asked.
  - The file is single-writer-per-process but multi-reader safe.
    Concurrent writers from multiple processes append-only on POSIX is
    atomic up to PIPE_BUF (4 KB); our events are << 4 KB.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

DEFAULT_EVENTS_PATH = Path("logs/events.jsonl")

Severity = Literal["info", "warn", "critical"]


def emit_event(
    type: str,
    summary: str,
    *,
    severity: Severity = "info",
    slug: str | None = None,
    data: dict[str, Any] | None = None,
    events_path: Path | None = None,
) -> None:
    """
    Append one structured event to the events log.

    Failure-tolerant: if the write fails (disk full, perms, etc.) we log a
    Python warning and move on rather than propagate — the calling code
    should never break because the event log is unavailable.
    """
    path = events_path or DEFAULT_EVENTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "type": type,
        "severity": severity,
        "slug": slug,
        "summary": summary,
        "data": data or {},
    }
    try:
        with path.open("a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as exc:
        log.warning("emit_event failed (%s): %s", type, exc)


def read_events(
    *,
    since_offset: int = 0,
    events_path: Path | None = None,
    severities: tuple[str, ...] | None = None,
    types: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Read events from `since_offset` (file byte position) forward.

    Returns `(events, new_offset)`. Pass `new_offset` back as
    `since_offset` next call to read only events appended since the last
    read. This is how the operator agent tails the file efficiently.

    Severity / type filters are applied AFTER reading so the offset still
    advances past skipped events (no duplicates on next read).
    """
    path = events_path or DEFAULT_EVENTS_PATH
    if not path.exists():
        return [], 0
    events: list[dict[str, Any]] = []
    with path.open("rb") as f:
        f.seek(since_offset)
        for line in f:
            try:
                ev = json.loads(line.decode("utf-8").strip())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if severities and ev.get("severity") not in severities:
                continue
            if types and ev.get("type") not in types:
                continue
            events.append(ev)
            if limit and len(events) >= limit:
                # Advance offset by what we've read so far.
                new_offset = f.tell()
                return events, new_offset
        new_offset = f.tell()
    return events, new_offset


def file_size(events_path: Path | None = None) -> int:
    """Current byte size of the events file (operator's `since_offset` baseline)."""
    path = events_path or DEFAULT_EVENTS_PATH
    return path.stat().st_size if path.exists() else 0
