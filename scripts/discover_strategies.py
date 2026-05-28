#!/usr/bin/env python3
"""
Discovery — promote spec-ready hypotheses into the lifecycle DB at PROPOSED.

New contract (post middle-refactor):
- Reads from the ingestion queue: every `ingestion_items` row at
  `SPEC_READY/PENDING` is a candidate.
- For each candidate, validates `<folder_path>/spec.md` against
  `trading_lab.agent.spec_validation`.
- Promotes in place: inserts a lifecycle hypothesis row keyed by
  `thesis_slug` and flips the ingestion row to `DISCOVERED/DONE`. No
  new artifact is written.

Legacy fallback:
- `--legacy-inbox` re-enables the old `manual_inbox/` drain path for the
  transition window (used by the migration tooling, not by crons).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--hypotheses-dir", type=Path, default=Path("research/hypotheses"))
    p.add_argument("--actor", default=f"agent:discover:{os.environ.get('USER','unknown')}")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-per-run", type=int, default=5)
    p.add_argument(
        "--legacy-inbox", action="store_true",
        help="Use the deprecated manual_inbox path instead of the ingestion queue.",
    )
    p.add_argument("--inbox", type=Path, default=Path("research/manual_inbox"))
    p.add_argument("--rss", action="store_true", help="(legacy) include RSS sources")
    p.add_argument("--sources", type=Path, default=Path("config/research.yaml"))
    args = p.parse_args()

    from trading_lab.agent import discovery, ingestion

    if args.legacy_inbox:
        candidates = discovery.scan_inbox(args.inbox, db_path=args.db)
        if args.rss:
            candidates += discovery.scan_rss(args.sources, db_path=args.db)
        if not candidates:
            print(json.dumps({"ok": True, "discovered": 0, "msg": "no candidates", "mode": "legacy"}))
            return 0
        results: list[dict] = []
        for cand in candidates[: args.max_per_run]:
            record = {"slug": cand.slug, "mode": "legacy"}
            if args.dry_run:
                record["dry_run"] = True
                results.append(record)
                continue
            try:
                registered = discovery.register_candidate(
                    cand,
                    db_path=args.db,
                    hypotheses_dir=args.hypotheses_dir,
                    actor=args.actor,
                    inbox_dir=args.inbox,
                )
                record["hypothesis_path"] = registered["hypothesis_path"]
            except Exception as exc:
                record["error"] = str(exc)
            results.append(record)
        print(json.dumps({
            "ok": True,
            "mode": "legacy",
            "discovered": len([r for r in results if "error" not in r and not r.get("dry_run")]),
            "errors": [r for r in results if "error" in r],
            "dry_run": args.dry_run,
            "details": results,
        }))
        return 0

    queue = ingestion.list_items(
        stage=ingestion.Stage.SPEC_READY.value,
        status=ingestion.Status.PENDING.value,
        db_path=args.db,
    )
    if not queue:
        print(json.dumps({"ok": True, "discovered": 0, "msg": "no spec-ready rows", "mode": "ingestion"}))
        return 0

    results: list[dict] = []
    for item in queue[: args.max_per_run]:
        record = {
            "intake_id": item.intake_id,
            "thesis_slug": item.thesis_slug or item.capture_slug,
            "spec_path": f"{item.folder_path}/spec.md",
        }
        if args.dry_run:
            record["dry_run"] = True
            results.append(record)
            continue
        try:
            registered = discovery.register_from_ingestion(
                item.intake_id,
                db_path=args.db,
                hypotheses_dir=args.hypotheses_dir,
                actor=args.actor,
            )
            record.update(registered)
        except Exception as exc:
            record["error"] = str(exc)
        results.append(record)

    print(json.dumps({
        "ok": True,
        "mode": "ingestion",
        "discovered": len([r for r in results if "error" not in r and not r.get("dry_run")]),
        "errors": [r for r in results if "error" in r],
        "dry_run": args.dry_run,
        "details": results,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
