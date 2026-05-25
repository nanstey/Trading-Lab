#!/usr/bin/env python3
"""
Run a PAPER hypothesis under `GenericPaperRunner` for a fixed duration.

Reads the hypothesis frontmatter for `strategy_module`, `strategy_class`,
`strategy_config_class`, and the `market_criteria` selecting which markets
to subscribe to.

Usage:
    .venv/bin/python scripts/paper_run.py --slug wide-spread-fade \\
        --duration-secs 300
"""

from __future__ import annotations

import argparse
import asyncio
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
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    from nautilus_predict.agent import lifecycle
    from nautilus_predict.config import load_config
    from nautilus_predict.data.market_catalog import MarketCatalog
    from nautilus_predict.data.market_filter import MarketCriteria, select_markets
    from nautilus_predict.runner.generic_paper import GenericPaperRunner

    h = lifecycle.get_hypothesis(args.slug)
    if not h:
        print(json.dumps({"ok": False, "error": "hypothesis_not_found"}))
        return 2
    if h.state not in (lifecycle.State.PAPER.value, lifecycle.State.PAPER_READY.value):
        print(json.dumps({
            "ok": False, "error": "wrong_state",
            "current": h.state,
            "hint": "Transition to PAPER first: scripts/transition_lifecycle.py "
                    "--slug <slug> --to PAPER --actor user:<name>",
        }))
        return 2

    if not (h.strategy_module and h.strategy_class and h.strategy_config_class):
        print(json.dumps({
            "ok": False, "error": "missing_strategy_refs",
            "hint": "Add strategy_module/strategy_class/strategy_config_class to "
                    "the hypothesis MD frontmatter and re-propose",
        }))
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

    # Pick the optimise-winner from the experiments table: highest PnL among
    # non-walk-forward grid rows whose params dict contains ONLY fields the
    # strategy's *Config actually exposes (filters out legacy
    # `{min_profit_usdc, max_capital_usdc}` rows from eval_strategy.py).
    strategy_params: dict = {}
    cfg_field_names: set[str] = set()
    if h.strategy_module and h.strategy_config_class:
        import importlib
        try:
            mod = importlib.import_module(h.strategy_module)
            cfg_cls = getattr(mod, h.strategy_config_class)
            # NT's StrategyConfig is msgspec.Struct → fields in __struct_fields__
            cfg_field_names = set(getattr(cfg_cls, "__struct_fields__", ()))
            if not cfg_field_names:
                cfg_field_names = set(getattr(cfg_cls, "model_fields", {}).keys())
        except Exception:
            cfg_field_names = set()

    exps = lifecycle.list_experiments(args.slug, limit=200)
    best_pnl = float("-inf")
    for e in exps:
        try:
            ep = json.loads(e.get("params_json") or "{}")
        except Exception:
            continue
        if "_wf_window" in ep:
            continue
        if (e.get("n_trades") or 0) < 30:
            continue
        # Skip rows whose params don't map to this strategy's config fields.
        if cfg_field_names and not (set(ep.keys()) & cfg_field_names):
            continue
        if (e.get("pnl") or 0) > best_pnl:
            best_pnl = e.get("pnl") or 0
            strategy_params = ep
    if strategy_params:
        logging.info("paper using optimised params: %s (best_pnl=%.2f)",
                     strategy_params, best_pnl)

    runner = GenericPaperRunner(
        config=cfg,
        slug=args.slug,
        strategy_module=h.strategy_module,
        strategy_class=h.strategy_class,
        strategy_config_class=h.strategy_config_class,
        pairs=pairs,
        strategy_params=strategy_params,
        duration_secs=args.duration_secs,
    )
    summary = asyncio.run(runner.run())
    out = {
        "ok": True,
        "slug": summary.slug,
        "strategy_class": summary.strategy_class,
        "instruments": summary.instruments,
        "signals_emitted": summary.signals_emitted,
        "cancels_emitted": summary.cancels_emitted,
        "log_path": summary.log_path,
        "kill_switch_triggered": summary.kill_switch_triggered,
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
