#!/usr/bin/env python3
"""
LIVE trading runner.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!!! THIS SUBMITS REAL ORDERS TO POLYMARKET WITH REAL USDC         !!
!!!! DOUBLE OPT-IN REQUIRED:                                       !!
!!!!     TRADING_MODE=live                                         !!
!!!!     LIVE_TRADING_CONFIRMED=true                               !!
!!!! Pre-flight checks refuse to start without all of:             !!
!!!!     - both env vars above                                     !!
!!!!     - L1 + L2 Polymarket credentials                          !!
!!!!     - global kill switch CLEAR                                !!
!!!!     - hypothesis state == LIVE                                !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Usage (REAL TRADING):
    TRADING_MODE=live LIVE_TRADING_CONFIRMED=true \\
        .venv/bin/python scripts/live_run.py --slug <slug> --duration-secs 3600
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--duration-secs", type=int, default=3600)
    p.add_argument(
        "--i-understand-this-is-live", action="store_true",
        help="Final confirmation flag — required to actually start. "
             "If omitted, the script just dry-runs the pre-flight checks.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from trading_lab.agent import lifecycle
    from trading_lab.config import load_config
    from trading_lab.data.market_catalog import MarketCatalog
    from trading_lab.data.market_filter import MarketCriteria, select_markets
    from trading_lab.runner.live_v2 import LiveRunner, LiveTradingNotEnabled

    h = lifecycle.get_hypothesis(args.slug)
    if not h:
        print(json.dumps({"ok": False, "error": "hypothesis_not_found"}))
        return 2
    if h.state != lifecycle.State.LIVE.value:
        print(json.dumps({
            "ok": False, "error": "wrong_state", "current": h.state,
            "hint": "Hypothesis state must be LIVE before live trading. "
                    "Transition via scripts/transition_lifecycle.py --slug "
                    "<slug> --to LIVE --actor user:<name> (human gate).",
        }))
        return 2
    if not (h.strategy_module and h.strategy_class and h.strategy_config_class):
        print(json.dumps({"ok": False, "error": "missing_strategy_refs"}))
        return 2

    cfg = load_config()
    crit = MarketCriteria.from_dict(h.market_criteria)
    cat = MarketCatalog(Path("data/market_catalog.db"))
    rows = select_markets(crit, cat)
    cat.close()
    pairs = [
        (r.condition_id, r.yes_token_id, r.no_token_id)
        for r in rows
        if r.yes_token_id and r.no_token_id
    ]
    if not pairs:
        print(json.dumps({"ok": False, "error": "no_markets_selected"}))
        return 2

    # Pick optimised params.
    strategy_params: dict = {}
    cfg_field_names: set[str] = set()
    try:
        import importlib

        mod = importlib.import_module(h.strategy_module)
        cfg_cls = getattr(mod, h.strategy_config_class)
        cfg_field_names = set(getattr(cfg_cls, "__struct_fields__", ()))
    except Exception:
        pass
    best_pnl = float("-inf")
    for e in lifecycle.list_experiments(args.slug, limit=200):
        try:
            ep = json.loads(e.get("params_json") or "{}")
        except Exception:
            continue
        if "_wf_window" in ep or "_paper_summary" in ep:
            continue
        if (e.get("n_trades") or 0) < 30:
            continue
        if cfg_field_names and not (set(ep.keys()) & cfg_field_names):
            continue
        if (e.get("pnl") or 0) > best_pnl:
            best_pnl = e.get("pnl") or 0
            strategy_params = ep

    # Pre-flight only mode (default — no --i-understand-this-is-live flag).
    if not args.i_understand_this_is_live:
        # Construct LiveRunner — pre-flight gates run in __init__.
        try:
            LiveRunner(
                config=cfg,
                slug=args.slug,
                strategy_module=h.strategy_module,
                strategy_class=h.strategy_class,
                strategy_config_class=h.strategy_config_class,
                pairs=pairs,
                strategy_params=strategy_params,
                duration_secs=args.duration_secs,
            )
        except LiveTradingNotEnabled as exc:
            print(json.dumps({"ok": False, "error": "pre_flight_failed",
                              "reason": str(exc)}))
            return 3
        print(json.dumps({
            "ok": True,
            "mode": "pre_flight_only",
            "slug": args.slug,
            "pairs": len(pairs),
            "strategy_params": strategy_params,
            "msg": "All pre-flight checks PASSED. Pass --i-understand-this-is-live "
                   "to actually start live trading.",
        }))
        return 0

    # Real live run.
    try:
        runner = LiveRunner(
            config=cfg,
            slug=args.slug,
            strategy_module=h.strategy_module,
            strategy_class=h.strategy_class,
            strategy_config_class=h.strategy_config_class,
            pairs=pairs,
            strategy_params=strategy_params,
            duration_secs=args.duration_secs,
        )
    except LiveTradingNotEnabled as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 3
    summary = runner.run()
    print(json.dumps({
        "ok": True,
        "slug": summary.slug,
        "instruments": summary.instruments,
        "duration_secs": summary.duration_secs,
        "kill_switch_triggered": summary.kill_switch_triggered,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
