#!/usr/bin/env python3
"""
Hyperliquid backtest CLI.

Three modes:
    --coin BTC                : single-symbol backtest
    --hypothesis-slug X       : load research/hypotheses/X.md and run multi-market
                                using the universe + tier from frontmatter
    --universe-as-of DATE     : with `--top-n` / `--tier`, ad-hoc multi-market

Strategy is loaded from the same `strategy_module`/`strategy_class`/`strategy_config_class`
fields the Polymarket hypotheses use; for ad-hoc runs supply `--strategy-*` flags.

Emits a JSON summary on stdout — same envelope downstream tools expect.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.data.hl_universe import (
    coin_names,
    filter_by_tier,
    load_universe,
)
from trading_lab.runner.hl_backtest import (
    HyperliquidBacktestRunner,
    HyperliquidFeeConfig,
)

log = logging.getLogger("hl_backtest")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--coin", help="Single coin (e.g. BTC).")
    g.add_argument("--hypothesis-slug", help="Load strategy + universe from a hypothesis MD.")
    g.add_argument("--coins", help="Comma list (e.g. BTC,ETH,SOL).")
    p.add_argument("--bar-interval", default="1h", choices=["5m", "1h", "1d"])
    p.add_argument("--start", required=True, help="UTC YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="UTC YYYY-MM-DD (exclusive)")
    p.add_argument("--initial-capital-usdc", type=float, default=10_000.0)
    p.add_argument("--data-dir", default="data/parquet")
    p.add_argument("--universe-as-of", help="Snapshot date for universe lookup (defaults to --end)")
    p.add_argument("--tiers", default="", help="Comma list of tiers (tier_1,tier_2,tier_3)")

    # Ad-hoc strategy flags (used when --hypothesis-slug is not set).
    p.add_argument("--strategy-module")
    p.add_argument("--strategy-class")
    p.add_argument("--strategy-config-class")
    p.add_argument("--strategy-params-json", default="{}")

    # Fee model overrides.
    p.add_argument("--maker-bps", type=float, default=None)
    p.add_argument("--taker-bps", type=float, default=None)

    p.add_argument("--no-funding", action="store_true", help="Skip funding PnL adjustment")
    p.add_argument("--log-level", default="WARNING")
    return p.parse_args(argv)


def _resolve_coins(args: argparse.Namespace, catalog: HyperliquidCatalog) -> list[str]:
    if args.coin:
        return [args.coin.upper()]
    if args.coins:
        return [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    # hypothesis path: load universe at --universe-as-of or --end
    as_of_raw = args.universe_as_of or args.end
    if hasattr(as_of_raw, "isoformat"):
        as_of_raw = as_of_raw.isoformat()
    as_of_date = datetime.strptime(str(as_of_raw), "%Y-%m-%d").replace(tzinfo=UTC)
    universe = load_universe(catalog, as_of_date)
    if not universe:
        raise SystemExit("No universe snapshot — run scripts/refresh_hl_universe.py first.")
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()] if args.tiers else None
    universe = filter_by_tier(universe, tiers)
    return coin_names(universe)


def _resolve_strategy(args: argparse.Namespace, hyp_fm: dict[str, Any] | None) -> dict[str, Any]:
    if hyp_fm:
        return {
            "strategy_module": hyp_fm["strategy_module"],
            "strategy_class": hyp_fm["strategy_class"],
            "strategy_config_class": hyp_fm.get("strategy_config_class"),
            "strategy_params": hyp_fm.get("strategy_params", {}) or {},
        }
    if not (args.strategy_module and args.strategy_class):
        raise SystemExit("Provide --strategy-module + --strategy-class (or --hypothesis-slug).")
    return {
        "strategy_module": args.strategy_module,
        "strategy_class": args.strategy_class,
        "strategy_config_class": args.strategy_config_class,
        "strategy_params": json.loads(args.strategy_params_json),
    }


def _read_hypothesis(slug: str) -> dict[str, Any]:
    """Parse research/hypotheses/<slug>.md frontmatter as a dict."""
    path = Path("research/hypotheses") / f"{slug}.md"
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"hypothesis {slug} has no YAML frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError(f"hypothesis {slug} frontmatter not terminated")
    import yaml

    return yaml.safe_load(text[3:end].strip()) or {}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)

    catalog = HyperliquidCatalog(Path(args.data_dir))

    hyp_fm: dict[str, Any] | None = None
    if args.hypothesis_slug:
        hyp_fm = _read_hypothesis(args.hypothesis_slug)
        # Hypothesis may pin its own bar interval and universe.
        args.bar_interval = hyp_fm.get("bar_interval", args.bar_interval)
        if not args.tiers:
            args.tiers = ",".join(hyp_fm.get("universe_tiers", []))
        if not args.universe_as_of:
            args.universe_as_of = hyp_fm.get("universe_as_of", args.end)

    coins = _resolve_coins(args, catalog)
    strategy_kw = _resolve_strategy(args, hyp_fm)

    fees = HyperliquidFeeConfig()
    if args.maker_bps is not None:
        fees.maker_bps = args.maker_bps
    if args.taker_bps is not None:
        fees.taker_bps = args.taker_bps

    runner = HyperliquidBacktestRunner(catalog, fees=fees, log_level=args.log_level.upper())

    use_funding = not args.no_funding
    if len(coins) == 1:
        r = runner.run_single(
            coin=coins[0],
            bar_interval=args.bar_interval,
            start=start,
            end=end,
            initial_capital_usdc=args.initial_capital_usdc,
            use_funding=use_funding,
            **strategy_kw,
        )
        out = {
            "mode": "single",
            "coin": coins[0],
            "bar_interval": args.bar_interval,
            "start": args.start,
            "end": args.end,
            "result": r.to_dict(),
        }
    else:
        r = runner.run_multi_market(
            coins=coins,
            bar_interval=args.bar_interval,
            start=start,
            end=end,
            initial_capital_usdc_per_market=args.initial_capital_usdc,
            use_funding=use_funding,
            **strategy_kw,
        )
        out = {
            "mode": "portfolio",
            "coins": coins,
            "bar_interval": args.bar_interval,
            "start": args.start,
            "end": args.end,
            "result": r.to_dict(),
        }

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
