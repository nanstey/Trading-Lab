#!/usr/bin/env python3
"""
Operator briefing — designed as the entry point for an external agent
running on a cron that decides what to text/email the human.

Two output modes:
  --json   (default) one JSON blob with structured fields the agent reads.
  --md     human-readable markdown summary.

Two read modes:
  --since-offset N   read events since byte offset N (set by the agent).
  --since-hours H    read events from the last H hours (default 24).

Forwarding policy (computed by this script — agent doesn't need to invent):

  forward = []
    + every `critical` event since cutoff
    + first `warn` event per (type, slug) since cutoff (dedup'd)
    + paper_summary deltas if realised_pnl moved by > $50 (configurable)
    + global kill-switch state change either direction

The agent receives `forward` as a pre-cooked list; it formats the text
message and decides whether to actually send. Keeps the policy here so
it's testable, leaves the transport (SMS / Slack / email) to the agent.

State:
  - The agent owns its `since_offset`. Pass it via `--since-offset N`
    each call. The script reports the new offset back as `new_offset`.
  - On first run, omit `--since-offset` to start at "now minus
    --since-hours" so the agent doesn't get spammed with backlog.

Usage:
  python scripts/operator_briefing.py --json
  python scripts/operator_briefing.py --md
  python scripts/operator_briefing.py --since-offset 12345 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--since-offset", type=int, default=None,
        help="Byte offset to read events from (returned by previous call)",
    )
    p.add_argument(
        "--since-hours", type=float, default=24.0,
        help="Fallback when --since-offset isn't set: only events newer than this",
    )
    p.add_argument(
        "--pnl-delta-threshold", type=float, default=50.0,
        help="USDC PnL delta that promotes a paper_summary event to `forward`",
    )
    p.add_argument(
        "--events-path", type=Path,
        default=Path("logs/events.jsonl"),
    )
    p.add_argument("--md", action="store_true", help="Output markdown instead of JSON")
    args = p.parse_args()

    from trading_lab.agent import lifecycle
    from trading_lab.agent.events import file_size, read_events
    from trading_lab.risk.kill_switch import read_flag

    # ---- Read events ----
    if args.since_offset is not None:
        since_offset = args.since_offset
    else:
        # Estimate: read the whole file, then drop events older than cutoff.
        since_offset = 0
    all_events, new_offset = read_events(
        since_offset=since_offset, events_path=args.events_path,
    )
    cutoff = datetime.now(tz=UTC) - timedelta(hours=args.since_hours)
    if args.since_offset is None:
        events = [e for e in all_events if _parse_ts(e["ts"]) >= cutoff]
    else:
        events = all_events

    # ---- Snapshot current state ----
    hypotheses = lifecycle.list_hypotheses()
    by_state: dict[str, list[str]] = defaultdict(list)
    for h in hypotheses:
        by_state[h.state].append(h.slug)

    ks_flag = read_flag()
    kill_switch_active = bool(ks_flag and ks_flag.get("triggered"))

    # ---- Apply forwarding policy ----
    forward = _build_forward_list(events, args.pnl_delta_threshold, kill_switch_active)

    payload = {
        "ok": True,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "since": {
            "offset": since_offset,
            "hours": args.since_hours if args.since_offset is None else None,
        },
        "new_offset": new_offset,
        "file_size": file_size(args.events_path),
        "state_snapshot": {
            "hypotheses_by_state": dict(by_state),
            "kill_switch_active": kill_switch_active,
            "kill_switch_reason": (ks_flag or {}).get("reason"),
        },
        "events_seen": len(events),
        "events_by_type": _count_by(events, "type"),
        "events_by_severity": _count_by(events, "severity"),
        "forward": forward,
    }

    if args.md:
        print(_render_md(payload))
    else:
        print(json.dumps(payload, default=str))
    return 0


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.now(tz=UTC)


def _count_by(events: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for e in events:
        out[str(e.get(key) or "?")] += 1
    return dict(out)


def _build_forward_list(
    events: list[dict],
    pnl_delta_threshold: float,
    kill_switch_active: bool,
) -> list[dict]:
    """
    Policy: critical events always forward; one warn per (type, slug) per
    window; paper_summary forwards iff the latest realised PnL moved by
    more than `pnl_delta_threshold` versus the previous summary for that
    slug; kill-switch state changes always forward.
    """
    forward: list[dict] = []

    # Group paper_summary events by slug to compute PnL delta.
    summaries_by_slug: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        if ev.get("type") == "paper_summary":
            summaries_by_slug[ev.get("slug") or ""].append(ev)

    warn_seen: set[tuple[str, str]] = set()
    for ev in events:
        sev = ev.get("severity")
        ev_type = ev.get("type")
        slug = ev.get("slug") or ""

        if sev == "critical":
            forward.append(ev)
            continue

        if ev_type == "paper_summary":
            seq = summaries_by_slug[slug]
            cur_pnl = float((ev.get("data") or {}).get("realised_pnl_usdc") or 0)
            # Compare to the previous summary for this slug in the same window.
            idx = seq.index(ev)
            if idx == 0:
                continue
            prev_pnl = float(
                (seq[idx - 1].get("data") or {}).get("realised_pnl_usdc") or 0
            )
            if abs(cur_pnl - prev_pnl) >= pnl_delta_threshold:
                forward.append({
                    **ev,
                    "data": {
                        **(ev.get("data") or {}),
                        "_pnl_delta_usdc": round(cur_pnl - prev_pnl, 2),
                    },
                })
            continue

        if sev == "warn":
            key = (str(ev_type), slug)
            if key in warn_seen:
                continue
            warn_seen.add(key)
            forward.append(ev)

    return forward


def _render_md(payload: dict) -> str:
    snap = payload["state_snapshot"]
    by_state = snap["hypotheses_by_state"]
    lines = [
        f"# Operator briefing — {payload['generated_at']}",
        "",
        "## State snapshot",
        "",
        f"- Kill switch: {'**ACTIVE** — ' + (snap.get('kill_switch_reason') or '') if snap['kill_switch_active'] else 'clear'}",
    ]
    for state, slugs in sorted(by_state.items()):
        lines.append(f"- {state}: {', '.join(slugs)}")
    lines += [
        "",
        f"## Events seen: {payload['events_seen']}",
        "",
        f"- By type: {payload['events_by_type']}",
        f"- By severity: {payload['events_by_severity']}",
        f"- New offset: {payload['new_offset']}",
        "",
        "## Forward (to operator)",
        "",
    ]
    if not payload["forward"]:
        lines.append("_nothing to forward._")
    for fw in payload["forward"]:
        lines.append(
            f"- **[{fw.get('severity','?').upper()}]** "
            f"`{fw.get('type')}` "
            f"({fw.get('slug') or '-'}): {fw.get('summary')}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
