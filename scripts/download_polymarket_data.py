#!/usr/bin/env python3
"""
Download historical Polymarket market data to Parquet.

Fetches trade history for a market (by condition_id — captures BOTH legs)
and optionally polls forward-going order book snapshots for a token.

Usage:
    python scripts/download_polymarket_data.py \\
        --condition-id 0xb8a8cd3... \\
        --start 2025-05-01 --end 2025-05-24

    # Forward-poll book snapshots for one token (live capture, blocks until end):
    python scripts/download_polymarket_data.py \\
        --book-token 462155... --book-duration-minutes 60 --book-only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Polymarket historical data to Parquet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--condition-id",
        nargs="*",
        default=[],
        metavar="COND_ID",
        help="Polymarket condition_id(s) to fetch trades for (captures YES+NO).",
    )
    parser.add_argument(
        "--book-token",
        nargs="*",
        default=[],
        metavar="TOKEN_ID",
        help="Token IDs to poll book snapshots for (forward-only).",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Trade history start date (default: 30 days ago)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Trade history end date (default: now)",
    )
    parser.add_argument(
        "--book-duration-minutes",
        type=int,
        default=0,
        help="If polling book snapshots, how long to run (minutes). 0 = skip.",
    )
    parser.add_argument(
        "--book-interval-secs",
        type=int,
        default=60,
        help="Seconds between book polls (default: 60)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/parquet"),
        help="Parquet output root (default: ./data/parquet)",
    )
    parser.add_argument("--trades-only", action="store_true", help="Skip book polling")
    parser.add_argument("--book-only", action="store_true", help="Skip trade history")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def parse_date(date_str: str | None, default_days_ago: int = 30) -> datetime:
    if date_str is None:
        return datetime.now(tz=UTC) - timedelta(days=default_days_ago)
    return datetime.fromisoformat(date_str).replace(tzinfo=UTC)


async def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from trading_lab.data.catalog import DataCatalog
    from trading_lab.data.ingestion import PolymarketDataIngester

    args.output_dir.mkdir(parents=True, exist_ok=True)
    catalog = DataCatalog(data_dir=args.output_dir)

    start_dt = parse_date(args.start, default_days_ago=30)
    end_dt = parse_date(args.end, default_days_ago=0)
    if end_dt <= start_dt:
        log.error("end must be after start (start=%s end=%s)", start_dt, end_dt)
        return 1

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    async with PolymarketDataIngester(catalog=catalog) as ing:
        if not args.book_only and args.condition_id:
            for cid in args.condition_id:
                log.info("fetching trades condition_id=%s", cid)
                n = await ing.fetch_historical_trades(cid, start_ts, end_ts)
                log.info("trades fetched condition_id=%s count=%s", cid, n)

        if (
            not args.trades_only
            and args.book_token
            and args.book_duration_minutes > 0
        ):
            book_start = int(datetime.now(tz=UTC).timestamp())
            book_end = book_start + args.book_duration_minutes * 60
            tasks = [
                ing.fetch_orderbook_snapshots(
                    tok, book_start, book_end, interval_sec=args.book_interval_secs
                )
                for tok in args.book_token
            ]
            results = await asyncio.gather(*tasks)
            log.info("book snapshots persisted total=%s", sum(results))

    summary = catalog.get_data_summary()
    print("\n=== Data Catalog Summary ===")
    print(f"Total markets: {summary['total_markets']}")
    print(f"Total Parquet files: {summary['total_parquet_files']}")
    print(f"Data directory: {summary['data_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
