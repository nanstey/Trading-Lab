#!/usr/bin/env python3
"""
Backfill Hyperliquid candles and funding history into the local Parquet catalog.

Usage:
    python scripts/download_hyperliquid_data.py \\
        --coins BTC,ETH,SOL \\
        --intervals 5m,1h,1d \\
        --start 2024-01-01 \\
        --end   2026-05-30 \\
        --include-funding

If --coins is omitted, we pull the current `meta.universe` (perp listings only,
excluding delisted) and operate over every coin. The pipeline is idempotent —
re-runs only rewrite partitions that already exist.

Prints a JSON summary on stdout with per-coin / per-interval row counts and any
errors so downstream scripts (and the agent loop) can act on the result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.venues.hyperliquid.endpoints import MAINNET_HTTP_URL
from trading_lab.venues.hyperliquid.historical import (
    HyperliquidHistoricalClient,
)

DEFAULT_DATA_DIR = Path("data/parquet")

log = logging.getLogger("hl_download")


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)


async def _resolve_universe(client: HyperliquidHistoricalClient) -> list[str]:
    meta = await client.get_meta()
    universe = meta.get("universe", [])
    return [
        u["name"]
        for u in universe
        if isinstance(u, dict) and not u.get("isDelisted", False)
    ]


async def _download_one_coin(
    client: HyperliquidHistoricalClient,
    catalog: HyperliquidCatalog,
    coin: str,
    intervals: list[str],
    start_ms: int,
    end_ms: int,
    include_funding: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"coin": coin, "intervals": {}, "errors": []}
    for interval in intervals:
        try:
            candles = await client.fetch_candles(coin, interval, start_ms, end_ms)
            rows_in = len(candles)
            catalog.write_candles(coin, interval, candles)
            summary = catalog.candles_summary(coin, interval)
            result["intervals"][interval] = {
                "fetched": rows_in,
                "stored_total": summary["rows"],
                "first": summary["first_ts_iso"],
                "last": summary["last_ts_iso"],
            }
            log.info(
                "candles_done",
                extra={
                    "coin": coin,
                    "interval": interval,
                    "fetched": rows_in,
                    "stored": summary["rows"],
                },
            )
        except Exception as exc:  # noqa: BLE001 — write to result, keep going
            log.exception("candles_failed", extra={"coin": coin, "interval": interval})
            result["errors"].append(f"candles[{interval}]: {exc!r}")
    if include_funding:
        try:
            funding = await client.fetch_funding_history(coin, start_ms, end_ms)
            written = catalog.write_funding(coin, funding)
            result["funding"] = {"fetched": len(funding), "stored": written}
        except Exception as exc:  # noqa: BLE001
            log.exception("funding_failed", extra={"coin": coin})
            result["errors"].append(f"funding: {exc!r}")
    return result


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    start_ms = int(_parse_date(args.start).timestamp() * 1000)
    end_ms = int(_parse_date(args.end).timestamp() * 1000)

    data_dir = Path(args.data_dir)
    catalog = HyperliquidCatalog(data_dir)

    async with HyperliquidHistoricalClient(MAINNET_HTTP_URL) as client:
        if args.coins:
            coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
        else:
            coins = await _resolve_universe(client)
            if args.limit_universe:
                coins = coins[: args.limit_universe]

        # Bound concurrency — HL throttles aggressively if hit too hard.
        sem = asyncio.Semaphore(args.concurrency)

        async def _one(c: str) -> dict[str, Any]:
            async with sem:
                return await _download_one_coin(
                    client,
                    catalog,
                    c,
                    intervals,
                    start_ms,
                    end_ms,
                    args.include_funding,
                )

        per_coin = await asyncio.gather(*[_one(c) for c in coins])

    summary: dict[str, Any] = {
        "data_dir": str(data_dir),
        "coins_requested": coins,
        "intervals": intervals,
        "start": args.start,
        "end": args.end,
        "include_funding": args.include_funding,
        "per_coin": per_coin,
    }
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--coins", default="", help="Comma list, e.g. BTC,ETH. Empty = full perp universe.")
    p.add_argument("--intervals", default="1h", help="Comma list of intervals: 5m,1h,1d.")
    p.add_argument("--start", required=True, help="UTC date YYYY-MM-DD (inclusive).")
    p.add_argument("--end", required=True, help="UTC date YYYY-MM-DD (exclusive).")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--include-funding", action="store_true", help="Also pull hourly funding history.")
    p.add_argument("--limit-universe", type=int, default=0, help="When --coins is empty, cap N coins.")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        summary = asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}))
        return 130
    print(json.dumps(summary, indent=2, default=str))
    any_errors = any(coin.get("errors") for coin in summary["per_coin"])
    return 1 if any_errors else 0


if __name__ == "__main__":
    sys.exit(main())
