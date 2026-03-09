#!/usr/bin/env python3
"""
Download historical Polymarket market data to Parquet.

Fetches order book snapshots and trade history for specified token IDs
and saves them to the local Parquet data catalog for backtesting.

Usage:
    python scripts/download_polymarket_data.py \\
        --token-id 0xabc123... \\
        --start 2024-01-01 \\
        --end 2024-03-31 \\
        --output-dir ./data/parquet

    # Multiple token IDs:
    python scripts/download_polymarket_data.py \\
        --token-id 0xabc123 0xdef456 \\
        --start 2024-01-01 \\
        --end 2024-01-31
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Download Polymarket historical data to Parquet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--token-id",
        nargs="+",
        required=True,
        metavar="TOKEN_ID",
        help="Polymarket outcome token ID(s) to fetch (0x-prefixed hex)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start date for data fetch (default: 30 days ago)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="End date for data fetch (default: today)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/parquet"),
        metavar="DIR",
        help="Output directory for Parquet files (default: ./data/parquet)",
    )
    parser.add_argument(
        "--mode",
        choices=["trades", "orderbook", "both"],
        default="both",
        help="What data to fetch (default: both)",
    )
    parser.add_argument(
        "--orderbook-interval",
        type=int,
        default=60,
        metavar="SECS",
        help="Seconds between orderbook snapshots (default: 60)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


def parse_date(date_str: str | None, default_days_ago: int = 30) -> datetime:
    """Parse a date string or return a default."""
    if date_str is None:
        from datetime import timedelta
        return datetime.now(tz=timezone.utc) - timedelta(days=default_days_ago)
    return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)


async def download_token(
    token_id: str,
    start_dt: datetime,
    end_dt: datetime,
    output_dir: Path,
    mode: str,
    orderbook_interval: int,
) -> None:
    """
    Download data for a single token ID.

    Parameters
    ----------
    token_id : str
        Polymarket outcome token ID.
    start_dt : datetime
        Start of data range.
    end_dt : datetime
        End of data range.
    output_dir : Path
        Root directory for Parquet output.
    mode : str
        "trades", "orderbook", or "both".
    orderbook_interval : int
        Seconds between orderbook snapshots.
    """
    from nautilus_predict.adapters.polymarket.auth import PolymarketAuth
    from nautilus_predict.adapters.polymarket.client import PolymarketClient
    from nautilus_predict.config import TradingConfig, PolymarketConfig
    from nautilus_predict.data.catalog import DataCatalog
    from nautilus_predict.data.ingestion import PolymarketDataIngester

    log.info(
        "Downloading data for token",
        extra={
            "token_id": token_id,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "mode": mode,
        },
    )

    # Load config from environment
    try:
        poly_config = PolymarketConfig()
    except Exception:
        # Fall back to public endpoints (no auth needed for data download)
        poly_config = PolymarketConfig(private_key="0" * 64)

    # Create a minimal auth object (no credentials needed for public data)
    auth = PolymarketAuth(private_key="0" * 64)

    catalog = DataCatalog(data_dir=output_dir)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    async with PolymarketClient(config=poly_config, auth=auth) as client:
        ingester = PolymarketDataIngester(client=client, catalog=catalog)

        if mode in ("trades", "both"):
            log.info("Fetching historical trades", extra={"token_id": token_id})
            trade_count = await ingester.fetch_historical_trades(
                token_id=token_id,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            log.info("Trades fetched", extra={"token_id": token_id, "count": trade_count})

        if mode in ("orderbook", "both"):
            log.info("Fetching orderbook snapshots", extra={"token_id": token_id})
            snapshot_count = await ingester.fetch_historical_orderbook_snapshots(
                token_id=token_id,
                start_ts=start_ts,
                end_ts=end_ts,
                interval_secs=orderbook_interval,
            )
            log.info(
                "Snapshots fetched",
                extra={"token_id": token_id, "count": snapshot_count},
            )


async def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    start_dt = parse_date(args.start, default_days_ago=30)
    end_dt = parse_date(args.end, default_days_ago=0)

    if end_dt <= start_dt:
        log.error("End date must be after start date")
        return 1

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        "Starting data download",
        extra={
            "token_count": len(args.token_id),
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "output_dir": str(output_dir),
            "mode": args.mode,
        },
    )

    # Download all tokens
    tasks = [
        download_token(
            token_id=token_id,
            start_dt=start_dt,
            end_dt=end_dt,
            output_dir=output_dir,
            mode=args.mode,
            orderbook_interval=args.orderbook_interval,
        )
        for token_id in args.token_id
    ]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        log.info("Download interrupted")
        return 130

    log.info("Download complete")

    # Print summary
    from nautilus_predict.data.catalog import DataCatalog
    catalog = DataCatalog(data_dir=output_dir)
    summary = catalog.get_data_summary()
    print("\n=== Data Catalog Summary ===")
    print(f"Total markets: {summary['total_markets']}")
    print(f"Total Parquet files: {summary['total_parquet_files']}")
    print(f"Data directory: {summary['data_dir']}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
