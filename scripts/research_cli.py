#!/usr/bin/env python3
"""
Research CLI — query/inspect facade over `research/experiments.db`.

Every subcommand prints JSON on stdout. Designed to be the single read-only
entry point for an autonomous agent runtime that needs to know "what's in
PROPOSED?", "what's the last experiment for slug X?", etc.

Subcommands:
    list       — list hypotheses (filter by state / rejection_category)
    show       — show one hypothesis with its history + last experiment
    history    — list lifecycle transitions for a slug
    experiments — list experiments for a slug
    budget     — show today's budget consumption
    init       — ensure schema exists (idempotent)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )

    p_list = sub.add_parser("list")
    p_list.add_argument("--state", default=None)
    p_list.add_argument("--category", dest="rejection_category", default=None)

    p_show = sub.add_parser("show")
    p_show.add_argument("--slug", required=True)

    p_hist = sub.add_parser("history")
    p_hist.add_argument("--slug", required=True)

    p_exp = sub.add_parser("experiments")
    p_exp.add_argument("--slug", default=None)
    p_exp.add_argument("--limit", type=int, default=20)
    p_exp.add_argument(
        "--sort",
        choices=["created_at", "pnl", "sharpe", "expectancy", "fill_rate", "score"],
        default="score",
        help="Sort experiments by methodology-aware score by default, or by a raw field.",
    )

    sub.add_parser("budget")
    sub.add_parser("init")

    args = p.parse_args()

    from trading_lab.agent import lifecycle
    from trading_lab.agent.budget import consumed
    from trading_lab.research.experiment_reporting import enrich_experiment_row, sort_experiments

    if args.cmd == "init":
        lifecycle.init_db(args.db)
        print(json.dumps({"db": str(args.db), "initialized": True}))
        return 0

    if args.cmd == "list":
        rows = lifecycle.list_hypotheses(
            state=args.state,
            rejection_category=args.rejection_category,
            db_path=args.db,
        )
        print(json.dumps([asdict(r) for r in rows], indent=2))
        return 0

    if args.cmd == "show":
        h = lifecycle.get_hypothesis(args.slug, db_path=args.db)
        if not h:
            print(json.dumps({"error": "not_found", "slug": args.slug}))
            return 1
        out = asdict(h)
        out["history"] = lifecycle.history(args.slug, db_path=args.db)
        out["experiments"] = sort_experiments(
            lifecycle.list_experiments(args.slug, db_path=args.db, limit=5),
            "score",
        )
        if out["experiments"]:
            out["last_experiment"] = out["experiments"][0]
        print(json.dumps(out, indent=2))
        return 0

    if args.cmd == "history":
        print(json.dumps(lifecycle.history(args.slug, db_path=args.db), indent=2))
        return 0

    if args.cmd == "experiments":
        rows = lifecycle.list_experiments(args.slug, db_path=args.db, limit=args.limit)
        print(json.dumps(sort_experiments(rows, args.sort), indent=2))
        return 0

    if args.cmd == "budget":
        print(json.dumps(consumed(db_path=args.db), indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
