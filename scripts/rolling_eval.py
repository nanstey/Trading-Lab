#!/usr/bin/env python3
"""
Rolling re-eval — runs eval_strategy.py on each PAPER strategy over
the last N days of catalog data. This is the "replay" use case: as
new data flows into the catalog (via run_ingestion.py), we can
periodically backtest each live strategy on fresh data and detect
regime change BEFORE a real loss accumulates.

Designed to run as a cron entry every few hours.

Usage:
    .venv/bin/python scripts/rolling_eval.py
    .venv/bin/python scripts/rolling_eval.py --window-days 7
    .venv/bin/python scripts/rolling_eval.py --states PAPER,OPTIMIZE
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("rolling_eval")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--window-days", type=float, default=2.0,
        help="Rolling backtest window length (default: 2 days — matches "
             "PM's typical historical retention).",
    )
    p.add_argument(
        "--states", default="PAPER",
        help="Comma-separated states to re-eval (default: PAPER). "
             "Common alternative: PAPER,OPTIMIZE",
    )
    p.add_argument(
        "--min-trades-floor", type=int, default=30,
        help="Decision-rule trade-count floor (passed to eval_strategy.py)",
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print which slugs WOULD be evaluated; don't actually run.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    from nautilus_predict.agent import lifecycle
    from nautilus_predict.agent.events import emit_event

    end_date = datetime.now(tz=UTC).date()
    start_date = (end_date - timedelta(days=args.window_days)).isoformat()
    end_iso = end_date.isoformat()

    states = [s.strip() for s in args.states.split(",")]
    targets: list[str] = []
    for state in states:
        for h in lifecycle.list_hypotheses(state=state, db_path=args.db):
            targets.append(h.slug)
    if not targets:
        print(json.dumps({"ok": True, "evaluated": 0, "msg": "no targets"}))
        return 0

    results: list[dict] = []
    for slug in targets:
        if args.dry_run:
            results.append({"slug": slug, "dry_run": True,
                            "window": f"{start_date}..{end_iso}"})
            continue

        # IMPORTANT: re-eval on a PAPER slug would normally demote it (per
        # the eval decision rules). We pass --no-transition so the rolling
        # eval just RECORDS the experiment but doesn't change state.
        # Watcher / human reviews the trend.
        # eval_strategy.py doesn't have --no-transition; we have to add it
        # OR pass a custom actor that the lifecycle gate rejects. Simplest:
        # for PAPER slugs, temporarily move them to a sentinel state that
        # eval can re-promote. Cleanest: just allow eval to potentially
        # demote and let the operator decide via the events log.
        cmd = [
            sys.executable, "scripts/eval_strategy.py",
            "--slug", slug,
            "--start", start_date,
            "--end", end_iso,
            "--min-trades-floor", str(args.min_trades_floor),
            "--actor", "agent:rolling_eval",
        ]
        env = os.environ.copy()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
        out_line = None
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                out_line = line
                break
        if proc.returncode != 0 or out_line is None:
            results.append({
                "slug": slug, "ok": False,
                "rc": proc.returncode, "stderr_tail": proc.stderr[-300:],
            })
            continue
        try:
            data = json.loads(out_line)
        except Exception:
            data = {"raw": out_line[:400]}
        results.append({"slug": slug, **data})

        emit_event(
            type="rolling_eval",
            summary=(
                f"{slug} rolling-eval ({start_date}..{end_iso}): "
                f"pnl=${data.get('pnl_usdc', 0):.2f} "
                f"n_trades={data.get('n_trades', 0)} "
                f"→ {data.get('decision_new_state', '?')}"
            ),
            severity="info",
            slug=slug,
            data={
                "window": f"{start_date}..{end_iso}",
                "pnl_usdc": data.get("pnl_usdc"),
                "n_trades": data.get("n_trades"),
                "sharpe": data.get("sharpe"),
                "decision_new_state": data.get("decision_new_state"),
            },
        )

    print(json.dumps({
        "ok": True,
        "evaluated": len([r for r in results if r.get("ok", False)]),
        "results": results,
        "window": f"{start_date}..{end_iso}",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
