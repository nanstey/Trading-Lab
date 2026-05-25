#!/usr/bin/env python3
"""
Auto-retirement watcher for PAPER strategies (Phase 5.7).

Reads paper-summary experiments rows (see `paper_summary.py`) per slug
and applies the protective rules:

  - Single-day loss > 5% of initial capital
        → state: PAPER → HALTED
        → category: single_day_drawdown
        → requires user review before resume.

  - 7-day rolling drawdown > 15% from peak equity
        → state: PAPER → RETIRED
        → category: drawdown_7d_15pct
        → cancels open orders (no-op in paper; real in live).

  - Global kill switch (`data/.kill_switch`) tripped
        → ALL PAPER strategies → HALTED
        → category: global_kill_switch

Driven by env-overridable thresholds:
  WATCHER_INITIAL_CAPITAL_USDC  — denominator for the loss ratios (default 10000)
  WATCHER_SINGLE_DAY_LIMIT_PCT  — default 5
  WATCHER_ROLLING_DD_LIMIT_PCT  — default 15
  WATCHER_ROLLING_WINDOW_DAYS   — default 7

Designed to run on a cron (every 5-10 min). Idempotent — re-running on
the same state takes no action.

JSON output: `{"checked": N, "halted": [...], "retired": [...]}`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

log = logging.getLogger("paper_watcher")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    p.add_argument(
        "--actor", default="agent:watcher",
        help="Audit-trail actor (don't override unless testing)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Compute thresholds + report but do NOT apply transitions",
    )
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from nautilus_predict.agent import lifecycle
    from nautilus_predict.risk.kill_switch import read_flag

    initial = _env_float("WATCHER_INITIAL_CAPITAL_USDC", 10_000.0)
    sd_limit_pct = _env_float("WATCHER_SINGLE_DAY_LIMIT_PCT", 5.0)
    dd_limit_pct = _env_float("WATCHER_ROLLING_DD_LIMIT_PCT", 15.0)
    window_days = _env_int("WATCHER_ROLLING_WINDOW_DAYS", 7)

    sd_threshold = -initial * sd_limit_pct / 100.0
    dd_threshold = -initial * dd_limit_pct / 100.0

    halted: list[dict] = []
    retired: list[dict] = []

    # Global kill switch: if tripped, halt every PAPER strategy.
    ks_flag = read_flag()
    if ks_flag and ks_flag.get("triggered"):
        log.warning("global kill switch tripped: %s", ks_flag)
        for h in lifecycle.list_hypotheses(state=lifecycle.State.PAPER.value, db_path=args.db):
            entry = {"slug": h.slug, "reason": "global_kill_switch"}
            if not args.dry_run:
                try:
                    lifecycle.transition(
                        slug=h.slug,
                        to_state=lifecycle.State.HALTED.value,
                        reason=f"global kill switch tripped: {ks_flag.get('reason')}",
                        actor=args.actor,
                        rejection_category="global_kill_switch",
                        db_path=args.db,
                    )
                except Exception as exc:
                    entry["error"] = str(exc)
            halted.append(entry)
        print(json.dumps({
            "ok": True, "checked": len(halted),
            "halted": halted, "retired": retired,
            "dry_run": args.dry_run,
        }))
        return 0

    paper_hypotheses = lifecycle.list_hypotheses(
        state=lifecycle.State.PAPER.value, db_path=args.db,
    )
    log.info("checking %d PAPER hypotheses", len(paper_hypotheses))

    for h in paper_hypotheses:
        summaries = _paper_summaries(h.slug, db_path=args.db)
        if not summaries:
            continue

        # Single-day check: yesterday-or-today summary PnL.
        today_pnl, today_date = summaries[0]["pnl"], summaries[0]["date"]
        if today_pnl <= sd_threshold:
            entry = {
                "slug": h.slug,
                "rule": "single_day_drawdown",
                "date": today_date,
                "pnl": today_pnl,
                "threshold": sd_threshold,
            }
            if not args.dry_run:
                try:
                    lifecycle.transition(
                        slug=h.slug,
                        to_state=lifecycle.State.HALTED.value,
                        reason=(
                            f"single-day loss ${today_pnl:.2f} <= threshold "
                            f"${sd_threshold:.2f} on {today_date}"
                        ),
                        actor=args.actor,
                        rejection_category="single_day_drawdown",
                        db_path=args.db,
                    )
                except Exception as exc:
                    entry["error"] = str(exc)
            halted.append(entry)
            continue

        # Rolling-window drawdown: cumulative PnL over the last
        # `window_days` summaries.
        cutoff = datetime.now(tz=UTC).date() - timedelta(days=window_days)
        window_summaries = [
            s for s in summaries
            if _parse_date(s["date"]) >= cutoff
        ]
        if not window_summaries:
            continue
        cum_pnl = sum(s["pnl"] for s in window_summaries)
        if cum_pnl <= dd_threshold:
            entry = {
                "slug": h.slug,
                "rule": "rolling_drawdown",
                "window_days": window_days,
                "cum_pnl": cum_pnl,
                "threshold": dd_threshold,
            }
            if not args.dry_run:
                try:
                    lifecycle.transition(
                        slug=h.slug,
                        to_state=lifecycle.State.RETIRED.value,
                        reason=(
                            f"{window_days}d drawdown ${cum_pnl:.2f} <= threshold "
                            f"${dd_threshold:.2f}"
                        ),
                        actor=args.actor,
                        rejection_category=f"drawdown_{window_days}d_{int(dd_limit_pct)}pct",
                        db_path=args.db,
                    )
                except Exception as exc:
                    entry["error"] = str(exc)
            retired.append(entry)

    print(json.dumps({
        "ok": True,
        "checked": len(paper_hypotheses),
        "thresholds": {
            "single_day_pct": sd_limit_pct,
            "rolling_dd_pct": dd_limit_pct,
            "window_days": window_days,
            "initial_capital_usdc": initial,
        },
        "halted": halted,
        "retired": retired,
        "dry_run": args.dry_run,
    }))
    return 0


def _paper_summaries(slug: str, db_path: Path) -> list[dict]:
    """Return paper-summary experiments rows, newest first."""
    from nautilus_predict.agent import lifecycle

    out: list[dict] = []
    for row in lifecycle.list_experiments(slug, db_path=db_path, limit=100):
        try:
            params = json.loads(row.get("params_json") or "{}")
        except Exception:
            continue
        if not params.get("_paper_summary"):
            continue
        date = str(params.get("date") or "")
        if not date:
            continue
        out.append({
            "date": date,
            "pnl": float(row.get("pnl") or 0.0),
            "n_trades": int(row.get("n_trades") or 0),
        })
    return out


def _parse_date(s: str):
    # Accept YYYYMMDD or YYYY-MM-DD.
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    return datetime.fromisoformat(s).date()


if __name__ == "__main__":
    sys.exit(main())
