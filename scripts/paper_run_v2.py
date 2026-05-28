#!/usr/bin/env python3
"""
Drive a PAPER strategy under the real NautilusTrader TradingNode.

Strategy → `submit_order` → real venue execution client (`is_paper=True`) →
the venue's paper-fill engine (no real venue writes) → real `OrderFilled` /
`OrderCanceled` events on the message bus.

Same code path as live trading; flipping `is_paper` to False in the
appropriate venue factory makes this LIVE. That's intentional — no
paper-vs-live code divergence.

Venue dispatch:
  - polymarket (default): `PaperRunnerV2` against the Polymarket CLOB.
  - hyperliquid          : `HyperliquidRunner(is_paper=True)`.

The venue is read from the hypothesis frontmatter (`venue: hyperliquid`)
and may be overridden on the CLI via `--venue`.

Usage:
    .venv/bin/python scripts/paper_run_v2.py --slug tick-mean-revert \\
        --duration-secs 120
    .venv/bin/python scripts/paper_run_v2.py --slug hl-smoke \\
        --venue hyperliquid --testnet --duration-secs 120
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


def _resolve_strategy_params(slug: str, strategy_module: str, strategy_config_class: str) -> dict:
    """Pick the best (max-pnl, >=30 trades) experiment row's params for slug."""
    from trading_lab.agent import lifecycle

    cfg_field_names: set[str] = set()
    try:
        import importlib

        mod = importlib.import_module(strategy_module)
        cfg_cls = getattr(mod, strategy_config_class)
        cfg_field_names = set(getattr(cfg_cls, "__struct_fields__", ()))
    except Exception:
        pass
    best_pnl = float("-inf")
    chosen: dict = {}
    for e in lifecycle.list_experiments(slug, limit=200):
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
            chosen = ep
    return chosen


def _run_polymarket(args, h, cfg) -> int:
    from trading_lab.data.market_catalog import MarketCatalog
    from trading_lab.data.market_filter import MarketCriteria, select_markets
    from trading_lab.runner.paper_v2 import PaperRunnerV2

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

    strategy_params = _resolve_strategy_params(
        args.slug, h.strategy_module, h.strategy_config_class,
    )
    if strategy_params:
        logging.info("paper_v2 using optimised params: %s", strategy_params)

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
        "venue": "polymarket",
        "slug": summary.slug,
        "instruments": summary.instruments,
        "duration_secs": summary.duration_secs,
        "kill_switch_triggered": summary.kill_switch_triggered,
        "log_path": summary.log_path,
        "signals_emitted": summary.signals_emitted,
    }))
    return 0


def _run_hyperliquid(args, h, cfg) -> int:
    from trading_lab.runner.hl_v2 import HyperliquidRunner, HyperliquidRunnerError

    symbols = list(h.market_criteria.get("symbols") or [])
    # Normalise: accept "BTC", "BTC-PERP", or "BTC-PERP.HYPERLIQUID".
    coins: list[str] = []
    for s in symbols:
        s = str(s).split(".")[0]
        s = s.replace("-PERP", "")
        if s:
            coins.append(s)
    if not coins:
        print(json.dumps({
            "ok": False,
            "error": "no_symbols",
            "hint": "hypothesis market_criteria.symbols must list at least one coin",
        }))
        return 2

    # Default network for paper: testnet if --testnet, else mainnet
    # (data subscriptions only; paper writes nothing on the network).
    network = "testnet" if args.testnet else cfg.venues.hyperliquid.default_network
    strategy_params = _resolve_strategy_params(
        args.slug, h.strategy_module, h.strategy_config_class,
    )
    try:
        runner = HyperliquidRunner(
            config=cfg,
            slug=args.slug,
            strategy_module=h.strategy_module,
            strategy_class=h.strategy_class,
            strategy_config_class=h.strategy_config_class,
            symbols=coins,
            is_paper=True,
            network=network,
            strategy_params=strategy_params,
            duration_secs=args.duration_secs,
        )
    except HyperliquidRunnerError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 3
    summary = runner.run()
    print(json.dumps({
        "ok": True,
        "venue": "hyperliquid",
        "slug": summary.slug,
        "instruments": summary.instruments,
        "network": summary.network,
        "is_paper": summary.is_paper,
        "duration_secs": summary.duration_secs,
    }))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--duration-secs", type=int, default=300)
    p.add_argument(
        "--venue",
        choices=("polymarket", "hyperliquid", "auto"),
        default="auto",
        help="Override the venue from the hypothesis frontmatter.",
    )
    p.add_argument(
        "--testnet", action="store_true",
        help="(Hyperliquid only) Use the testnet endpoints for the data "
             "subscription. Paper writes nothing on the network either way.",
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

    venue = args.venue if args.venue != "auto" else h.venue
    cfg = load_config()
    if venue == "hyperliquid":
        return _run_hyperliquid(args, h, cfg)
    return _run_polymarket(args, h, cfg)


if __name__ == "__main__":
    sys.exit(main())
