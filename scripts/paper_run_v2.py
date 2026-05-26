#!/usr/bin/env python3
"""
Drive a PAPER strategy under the real NautilusTrader TradingNode.

Strategy → `submit_order` → real `PolymarketExecutionClient` →
`PolymarketPaperFillEngine` (no real venue calls) → real `OrderFilled` /
`OrderCanceled` events on the message bus.

Same code path as live trading; flipping `is_paper` to False in
`venues/polymarket/factory.py:PolymarketLiveExecClientFactory` makes
this LIVE. That's intentional — no paper-vs-live code divergence.

Usage:
    .venv/bin/python scripts/paper_run_v2.py --slug tick-mean-revert \\
        --duration-secs 120
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
    p.add_argument("--duration-secs", type=int, default=300)
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
    from trading_lab.runner.paper_v2 import PaperRunnerV2

    h = lifecycle.get_hypothesis(args.slug)
    if not h:
        print(json.dumps({"ok": False, "error": "hypothesis_not_found"}))
        return 2
    if h.state not in (
        lifecycle.State.PAPER.value,
        lifecycle.State.PAPER_READY.value,
    ):
        print(json.dumps({
            "ok": False, "error": "wrong_state", "current": h.state,
            "hint": "must be PAPER or PAPER_READY",
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

    # Pull the optimise-winner params from the experiments table — same
    # logic as paper_run.py.
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
    if strategy_params:
        logging.info(
            "paper_v2 using optimised params: %s (best_pnl=%.2f)",
            strategy_params, best_pnl,
        )

    runner = PaperRunnerV2(
        config=cfg,
        slug=args.slug,
        strategy_module=h.strategy_module,
        strategy_class=h.strategy_class,
        strategy_config_class=h.strategy_config_class,
        pairs=pairs,
        strategy_params=strategy_params,
        duration_secs=args.duration_secs,
    )
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
