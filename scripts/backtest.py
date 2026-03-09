#!/usr/bin/env python3
"""
Backtesting runner script.

Provides a CLI for running the system against historical Parquet data.
Backtesting uses identical strategy code as live trading — no code-path
divergence.

Usage:
    python scripts/backtest.py
    python scripts/backtest.py --start 2024-01-01 --end 2024-03-31
    python scripts/backtest.py --markets 0xabc123,0xdef456
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nautilus-Predict backtesting runner")
    parser.add_argument("--start", type=str, default=None, help="Start date ISO-8601")
    parser.add_argument("--end", type=str, default=None, help="End date ISO-8601")
    parser.add_argument(
        "--markets",
        type=str,
        default=None,
        help="Comma-separated condition IDs to include",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    markets = args.markets.split(",") if args.markets else None

    from nautilus_predict.backtest import run_backtest_session

    await run_backtest_session(
        start_iso=args.start,
        end_iso=args.end,
        instruments=markets,
    )


if __name__ == "__main__":
    asyncio.run(main())
