#!/usr/bin/env python3
"""
Drain `research/manual_inbox/` into PROPOSED hypotheses.

For each `*.md` file in the inbox: dedup (URL hash + similarity), sanitize
(strip imperative second-person sentences), materialise to
`research/hypotheses/<slug>.md`, insert DB row in PROPOSED. Move processed
inbox file to `research/manual_inbox/.archived/<date>/<slug>.md`.

Prints JSON: `{discovered: N, dedup_skipped: M, archived: ...}`.

Future work: walk `research/sources.yaml` RSS feeds and enqueue candidates
the same way. Not implemented yet — manual_inbox is the primary path.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inbox", type=Path, default=Path("research/manual_inbox"))
    p.add_argument("--hypotheses-dir", type=Path, default=Path("research/hypotheses"))
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--actor", default=f"agent:discover:{os.environ.get('USER','unknown')}")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done; don't write hypotheses or DB rows.",
    )
    p.add_argument(
        "--max-per-run", type=int, default=5,
        help="Cap number of candidates processed per invocation",
    )
    p.add_argument(
        "--rss", action="store_true",
        help="Also scan research/sources.yaml RSS feeds in addition to the inbox",
    )
    p.add_argument(
        "--sources", type=Path, default=Path("research/sources.yaml"),
    )
    args = p.parse_args()

    from trading_lab.agent import discovery, lifecycle

    candidates = discovery.scan_inbox(args.inbox, db_path=args.db)
    if args.rss:
        candidates += discovery.scan_rss(args.sources, db_path=args.db)
    if not candidates:
        print(json.dumps({"ok": True, "discovered": 0, "msg": "no candidates"}))
        return 0

    discovered: list[dict] = []
    archived_root = args.inbox / ".archived" / datetime.now(tz=UTC).strftime("%Y-%m-%d")

    for cand in candidates[: args.max_per_run]:
        record = {
            "slug": cand.slug,
            "dedup_candidates": cand.dedup_candidates,
            "prior_attempts": cand.prior_attempts,
        }
        if args.dry_run:
            record["dry_run"] = True
            discovered.append(record)
            continue

        md_path = discovery.candidate_to_hypothesis_md(cand, args.hypotheses_dir)
        try:
            lifecycle.add_hypothesis(
                slug=cand.slug,
                state=lifecycle.State.PROPOSED.value,
                source_url=cand.source_url,
                source_type=cand.source_type,
                summary=cand.summary[:1000],
                market_criteria=cand.market_criteria,
                actor=args.actor,
                db_path=args.db,
            )
        except Exception as exc:
            record["error"] = str(exc)
            discovered.append(record)
            continue

        # Archive the source file.
        src = args.inbox / f"{cand.slug}.md"
        if src.exists():
            archived_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(archived_root / src.name))

        record["hypothesis_path"] = str(md_path)
        discovered.append(record)

    print(json.dumps({
        "ok": True,
        "discovered": len([d for d in discovered if "error" not in d and not d.get("dry_run")]),
        "errors": [d for d in discovered if "error" in d],
        "dry_run": args.dry_run,
        "details": discovered,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
