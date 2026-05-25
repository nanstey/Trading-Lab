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

    from nautilus_predict.config import load_config
    from nautilus_predict.runner.backtest import BacktestRunner, BacktestRunResult

    cfg = load_config()
    if args.min_profit_usdc is not None:
        cfg.arb.min_profit_usdc = args.min_profit_usdc
    if args.max_capital_usdc is not None:
        cfg.arb.max_capital_usdc = args.max_capital_usdc

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    runner = BacktestRunner(config=cfg)
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
        print(f"Markets backtested: {len(summary['per_market'])}")
        for m in summary["per_market"]:
            print(
                f"  - {m['condition_id'][:14]}... "
                f"ticks={m['n_trade_ticks']} orders={m['n_orders']} "
                f"fills={m['n_fills']} pnl=${m['pnl_usdc']:,.2f} "
                f"sharpe={m['sharpe']:.2f} dd={m['max_drawdown_pct']:.1f}%"
            )
            print(f"    {m['question'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
