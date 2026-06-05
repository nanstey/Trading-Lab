#!/usr/bin/env python3
"""
Run a NautilusTrader backtest.

Two modes:

    # Hypothesis-driven (preferred — uses select_markets)
    python scripts/backtest.py --hypothesis-slug arb-complement \\
        --start 2026-05-15 --end 2026-05-25

    # Ad-hoc single pair
    python scripts/backtest.py --condition-id 0x... \\
        --yes-token-id <id> --no-token-id <id> \\
        --start 2026-05-15 --end 2026-05-25
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hypothesis-slug", default=None)
    p.add_argument("--condition-id", default=None)
    p.add_argument("--yes-token-id", default=None)
    p.add_argument("--no-token-id", default=None)
    p.add_argument("--start", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--end", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--initial-capital-usdc", type=float, default=10_000.0)
    p.add_argument(
        "--min-profit-usdc", type=float, default=None,
        help="Override ARB_MIN_PROFIT_USDC from .env",
    )
    p.add_argument(
        "--max-capital-usdc", type=float, default=None,
        help="Override ARB_MAX_CAPITAL_USDC from .env",
    )
    p.add_argument("--json", action="store_true", help="Print results as JSON line")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    import os

    from trading_lab.config import load_config
    from trading_lab.runner.backtest import BacktestRunner, BacktestRunResult

    cfg = load_config()
    # CLI overrides used to mutate cfg.arb in-place. That field is gone now
    # (strategy params live in the hypothesis MD + DB, not system config).
    # Translate the legacy flags into env vars BacktestRunner forwards.
    if args.min_profit_usdc is not None or args.max_capital_usdc is not None:
        params: dict = json.loads(os.environ.get("NP_STRATEGY_PARAMS_JSON") or "{}")
        if args.min_profit_usdc is not None:
            params["min_profit_usdc"] = args.min_profit_usdc
        if args.max_capital_usdc is not None:
            params["max_capital_usdc"] = args.max_capital_usdc
        os.environ["NP_STRATEGY_PARAMS_JSON"] = json.dumps(params)

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    # When called as a subprocess from BacktestRunner.run_hypothesis, the
    # strategy refs come in via env vars (NP_STRATEGY_MODULE etc) so the
    # ad-hoc --condition-id path uses the same strategy as the hypothesis
    # would. For the --hypothesis-slug path, run_hypothesis reads the
    # frontmatter itself.
    strategy_params: dict = {}
    raw_params = os.environ.get("NP_STRATEGY_PARAMS_JSON")
    if raw_params:
        try:
            strategy_params = json.loads(raw_params)
        except Exception:
            strategy_params = {}

    runner = BacktestRunner(
        config=cfg,
        strategy_module=os.environ.get("NP_STRATEGY_MODULE") or None,
        strategy_class=os.environ.get("NP_STRATEGY_CLASS") or None,
        strategy_config_class=os.environ.get("NP_STRATEGY_CONFIG_CLASS") or None,
        strategy_params=strategy_params,
    )
    if args.hypothesis_slug:
        result = runner.run_hypothesis(
            hypothesis_slug=args.hypothesis_slug,
            start=start,
            end=end,
            initial_capital_usdc=args.initial_capital_usdc,
        )
    else:
        if not (args.condition_id and args.yes_token_id and args.no_token_id):
            print(
                "ERROR: provide --hypothesis-slug OR "
                "(--condition-id, --yes-token-id, --no-token-id)",
                file=sys.stderr,
            )
            return 2
        single = runner.run_pair(
            condition_id=args.condition_id,
            yes_token_id=args.yes_token_id,
            no_token_id=args.no_token_id,
            start=start,
            end=end,
            initial_capital_usdc=args.initial_capital_usdc,
        )
        result = BacktestRunResult(
            per_market=[single],
            aggregate_pnl_usdc=single.pnl_usdc,
            aggregate_n_fills=single.n_fills,
            aggregate_n_orders=single.n_orders,
            mean_sharpe=single.sharpe,
        )

    summary = result.to_dict()
    if args.json:
        print(json.dumps(summary))
    else:
        print("\n=== Backtest Summary ===")
        print(f"Aggregate PnL:      ${summary['aggregate_pnl_usdc']:,.2f}")
        print(f"Total orders:       {summary['aggregate_n_orders']}")
        print(f"Total fills:        {summary['aggregate_n_fills']}")
        print(f"Mean Sharpe:        {summary['mean_sharpe']:.3f}")
        print(f"Expectancy/fill:    ${summary.get('aggregate_expectancy_usdc', 0.0):,.4f}")
        print(f"Aggregate fill rate:{summary.get('aggregate_fill_rate', 0.0):.3f}")
        print(f"Markets backtested: {len(summary['per_market'])}")
        print(f"Markets w/ fills:   {summary.get('n_markets_with_fills', 0)}")
        for m in summary["per_market"]:
            print(
                f"  - {m['condition_id'][:14]}... "
                f"ticks={m['n_trade_ticks']} orders={m['n_orders']} "
                f"fills={m['n_fills']} pnl=${m['pnl_usdc']:,.2f} "
                f"sharpe={m['sharpe']:.2f} dd={m['max_drawdown_pct']:.1f}% "
                f"exp=${m.get('expectancy_usdc', 0.0):.4f} pf={m.get('profit_factor', 0.0):.2f} "
                f"ls={m.get('longest_losing_streak', 0)}"
            )
            print(f"    {m['question'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
