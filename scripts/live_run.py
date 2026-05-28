#!/usr/bin/env python3
"""
LIVE / TESTNET trading runner.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!!! Mainnet runs submit REAL ORDERS with REAL FUNDS.              !!
!!!! Required for MAINNET (real money):                            !!
!!!!     LIVE_TRADING_CONFIRMED=true                                !!
!!!!     hypothesis state == LIVE                                   !!
!!!!     --i-understand-this-is-live CLI flag                       !!
!!!! TESTNET (Hyperliquid only) uses faucet USDC and skips both     !!
!!!! the env gate and the --i-understand flag. Pass --testnet.      !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Venue dispatch:
  - polymarket (default): `LiveRunner` against Polymarket mainnet.
  - hyperliquid          : `HyperliquidRunner` against either testnet
                           (--testnet) or mainnet (--i-understand-...).

Usage (Polymarket mainnet, REAL TRADING):
    LIVE_TRADING_CONFIRMED=true \\
        .venv/bin/python scripts/live_run.py --slug <slug> \\
            --i-understand-this-is-live --duration-secs 3600

Usage (Hyperliquid testnet):
    .venv/bin/python scripts/live_run.py --slug hl-smoke \\
        --venue hyperliquid --testnet --duration-secs 600
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


def _run_polymarket_live(args, h, cfg) -> int:
    """Polymarket mainnet — original triple-gate flow."""
    from trading_lab.data.market_catalog import MarketCatalog
    from trading_lab.data.market_filter import MarketCriteria, select_markets
    from trading_lab.runner.live_v2 import LiveRunner, LiveTradingNotEnabled

    if h.state != "LIVE":
        print(json.dumps({
            "ok": False, "error": "wrong_state", "current": h.state,
            "hint": "Hypothesis state must be LIVE for Polymarket live trading.",
        }))
        return 2

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

    if not args.i_understand_this_is_live:
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
            "venue": "polymarket",
            "slug": args.slug,
            "pairs": len(pairs),
            "strategy_params": strategy_params,
            "msg": "All pre-flight checks PASSED. Pass --i-understand-this-is-live "
                   "to actually start live trading.",
        }))
        return 0

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
        "venue": "polymarket",
        "slug": summary.slug,
        "instruments": summary.instruments,
        "duration_secs": summary.duration_secs,
        "kill_switch_triggered": summary.kill_switch_triggered,
    }))
    return 0


def _run_hyperliquid(args, h, cfg) -> int:
    """Hyperliquid testnet OR mainnet (depending on flags)."""
    from trading_lab.runner.hl_v2 import HyperliquidRunner, HyperliquidRunnerError

    # Flag resolution:
    #   --testnet                    → testnet (LIVE_READY+ required)
    #   --i-understand-this-is-live  → mainnet (LIVE required + env gate)
    #   neither                      → REFUSE (don't accidentally hit mainnet)
    #   both                         → REFUSE (mutually exclusive)
    if args.testnet and args.i_understand_this_is_live:
        print(json.dumps({
            "ok": False,
            "error": "conflicting_flags",
            "hint": "--testnet and --i-understand-this-is-live are mutually exclusive",
        }))
        return 2
    if not args.testnet and not args.i_understand_this_is_live:
        print(json.dumps({
            "ok": False,
            "error": "mainnet_flag_required",
            "hint": "Pass --testnet for HL testnet, or "
                    "--i-understand-this-is-live for HL mainnet (real money).",
        }))
        return 2

    if args.testnet:
        network = "testnet"
        # Testnet acts as the LIVE_READY shakedown — LIVE_READY or higher.
        if h.state not in ("LIVE_READY", "LIVE"):
            print(json.dumps({
                "ok": False, "error": "wrong_state", "current": h.state,
                "hint": "Hypothesis state must be >= LIVE_READY for HL testnet.",
            }))
            return 2
    else:
        network = "mainnet"
        if h.state != "LIVE":
            print(json.dumps({
                "ok": False, "error": "wrong_state", "current": h.state,
                "hint": "Hypothesis state must be LIVE for HL mainnet.",
            }))
            return 2

    symbols = list(h.market_criteria.get("symbols") or [])
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
            is_paper=False,
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
    p.add_argument("--duration-secs", type=int, default=3600)
    p.add_argument(
        "--venue",
        choices=("polymarket", "hyperliquid", "auto"),
        default="auto",
        help="Override the venue from the hypothesis frontmatter.",
    )
    p.add_argument(
        "--testnet", action="store_true",
        help="(Hyperliquid only) Route to testnet endpoints with faucet USDC. "
             "Skips the LIVE_TRADING_CONFIRMED env gate.",
    )
    p.add_argument(
        "--i-understand-this-is-live", action="store_true",
        help="Final confirmation flag for mainnet runs (real money). Required "
             "for Polymarket live and for Hyperliquid mainnet.",
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
    if not (h.strategy_module and h.strategy_class and h.strategy_config_class):
        print(json.dumps({"ok": False, "error": "missing_strategy_refs"}))
        return 2

    venue = args.venue if args.venue != "auto" else h.venue
    cfg = load_config()
    if venue == "hyperliquid":
        return _run_hyperliquid(args, h, cfg)
    return _run_polymarket_live(args, h, cfg)


if __name__ == "__main__":
    sys.exit(main())
