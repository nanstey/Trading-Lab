#!/usr/bin/env python3
"""
Move a hypothesis between lifecycle states (single atomic write).

This is the ONLY supported way for agents to change `hypotheses.state`. The
human-gated `PAPER_READY → PAPER` and `LIVE_READY → LIVE` transitions refuse
to run unless `--actor` starts with "user".

Usage:
    python scripts/transition_lifecycle.py --slug arb-complement \\
        --to BACKTEST --reason "ready for grid eval"

Prints JSON on success.
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
    p.add_argument("--slug", required=True)
    p.add_argument("--to", dest="to_state", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument(
        "--actor",
        default=f"user:{os.environ.get('USER', 'unknown')}",
        help='Required to be "user*" for human-gated transitions',
    )
    p.add_argument(
        "--rejection-category",
        default=None,
        help="Required when transitioning to REJECTED",
    )
    p.add_argument(
        "--override",
        action="store_true",
        help="Tag the transition as a manual override (audit-only)",
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    args = p.parse_args()

    from nautilus_predict.agent import lifecycle

    actor = args.actor + (":override" if args.override else "")
    try:
        lifecycle.transition(
            slug=args.slug,
            to_state=args.to_state,
            reason=args.reason,
            actor=actor,
            rejection_category=args.rejection_category,
            db_path=args.db,
        )
    except (ValueError, PermissionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    print(json.dumps({
        "ok": True,
        "slug": args.slug,
        "to_state": args.to_state,
        "actor": actor,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
