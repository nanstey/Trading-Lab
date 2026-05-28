#!/usr/bin/env python3
"""Inspect the upstream ingestion queue.

Subcommands:
  list                              # all rows
  list --stage <STAGE>              # filter by stage
  list --stage <STAGE> --status <S> # both
  show --intake-id <id>             # full row + event history
  show --slug <slug>                # ditto, looked up by slug
  next --stage <STAGE>              # the oldest PENDING row for a cron
  stale --older-than <duration>     # rows whose updated_at is older than e.g. 3d/6h

Output: text by default, --json for machine-readable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent import ingestion  # noqa: E402


def _parse_duration(text: str) -> timedelta:
    m = re.fullmatch(r"(\d+)\s*([smhd])", text.strip())
    if not m:
        raise ValueError(f"invalid duration: {text!r} (use e.g. 30m, 6h, 3d)")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def _item_summary(it: ingestion.IngestionItem) -> dict:
    return {
        "intake_id": it.intake_id,
        "capture_slug": it.capture_slug,
        "thesis_slug": it.thesis_slug,
        "stage": it.stage,
        "status": it.status,
        "next_action": it.next_action,
        "source_url": it.source_url,
        "folder_path": it.folder_path,
        "updated_at": it.updated_at,
    }


def cmd_list(args) -> int:
    items = ingestion.list_items(stage=args.stage, status=args.status, db_path=args.db)
    payload = [_item_summary(it) for it in items]
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not items:
            print("(no rows)")
        for it in items:
            slug = it.thesis_slug or it.capture_slug
            print(f"  {it.intake_id:>4}  {it.stage:<16} {it.status:<11} {slug}")
    return 0


def cmd_show(args) -> int:
    if args.intake_id:
        item = ingestion.get(args.intake_id, db_path=args.db)
    elif args.slug:
        item = ingestion.get_by_slug(args.slug, db_path=args.db)
    else:
        print("pass --intake-id or --slug", file=sys.stderr)
        return 2
    if item is None:
        print("not found", file=sys.stderr)
        return 2
    hist = ingestion.history(item.intake_id, db_path=args.db)
    if args.json:
        print(json.dumps({"item": _item_summary(item), "events": hist}, indent=2, default=str))
        return 0
    print(json.dumps(_item_summary(item), indent=2))
    print()
    print("events:")
    for e in hist:
        print(f"  {e['timestamp']}  {e['action']}  {e['from_stage']} -> {e['to_stage']}  ({e['actor']})")
    return 0


def cmd_next(args) -> int:
    if not args.stage:
        print("--stage is required for next", file=sys.stderr)
        return 2
    item = ingestion.next_pending(args.stage, db_path=args.db)
    if item is None:
        if args.json:
            print("null")
        else:
            print("[SILENT]")
        return 0
    if args.json:
        print(json.dumps(_item_summary(item), indent=2, default=str))
    else:
        slug = item.thesis_slug or item.capture_slug
        print(f"{item.intake_id}\t{item.stage}\t{slug}\t{item.folder_path}")
    return 0


def cmd_stale(args) -> int:
    if not args.older_than:
        print("--older-than is required (e.g. 3d, 6h)", file=sys.stderr)
        return 2
    delta = _parse_duration(args.older_than)
    cutoff = datetime.now(tz=UTC) - delta
    items = ingestion.list_items(db_path=args.db)
    stale = []
    for it in items:
        try:
            ts = datetime.fromisoformat(it.updated_at)
        except Exception:
            continue
        if ts < cutoff and it.status != ingestion.Status.DONE.value and it.stage not in ingestion.TERMINAL_STAGES:
            stale.append(it)
    if args.json:
        print(json.dumps([_item_summary(it) for it in stale], indent=2, default=str))
    else:
        if not stale:
            print("(no stale rows)")
        for it in stale:
            slug = it.thesis_slug or it.capture_slug
            print(f"  {it.intake_id:>4}  {it.stage:<16} {it.status:<11} {slug}  (updated {it.updated_at})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sl = sub.add_parser("list", help="list ingestion rows")
    sl.add_argument("--stage")
    sl.add_argument("--status")
    sl.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="show one row + history")
    sh.add_argument("--intake-id", type=int)
    sh.add_argument("--slug")
    sh.set_defaults(func=cmd_show)

    sn = sub.add_parser("next", help="oldest PENDING row at the given stage")
    sn.add_argument("--stage", required=True)
    sn.set_defaults(func=cmd_next)

    st = sub.add_parser("stale", help="rows updated longer ago than --older-than")
    st.add_argument("--older-than", required=True, help="e.g. 30m, 6h, 3d")
    st.set_defaults(func=cmd_stale)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
