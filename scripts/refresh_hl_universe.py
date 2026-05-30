#!/usr/bin/env python3
"""
Refresh the Hyperliquid top-N universe snapshot.

Usage:
    python scripts/refresh_hl_universe.py --as-of 2026-05-30 --top-n 20

Writes a point-in-time Parquet at:
    data/parquet/hyperliquid/universe_snapshots/<YYYY-MM-DD>.parquet

Backtests resolve their working symbol set through `load_universe(as_of=...)`,
so this script's cadence (monthly is fine) directly controls what counts as
the "live" universe during any historical window — and ensures coins that
have since been delisted or demoted still appear in the windows they were
top-20 in.

Prints JSON summary on stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.data.hl_universe import (
    compute_universe,
    persist_snapshot,
    universe_as_dicts,
)
from trading_lab.venues.hyperliquid.endpoints import MAINNET_HTTP_URL
from trading_lab.venues.hyperliquid.historical import (
    HyperliquidHistoricalClient,
)

DEFAULT_DATA_DIR = Path("data/parquet")

log = logging.getLogger("hl_universe_refresh")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--as-of", default=datetime.now(UTC).date().isoformat(),
                   help="Snapshot date label, YYYY-MM-DD. Defaults to today.")
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--method", choices=["live_24h", "historical_30d"], default="live_24h")
    p.add_argument("--lookback-days", type=int, default=30)
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> dict[str, object]:
    catalog = HyperliquidCatalog(Path(args.data_dir))
    async with HyperliquidHistoricalClient(MAINNET_HTTP_URL) as client:
        universe = await compute_universe(
            client,
            top_n=args.top_n,
            method=args.method,
            lookback_days=args.lookback_days,
            catalog=catalog,
            as_of=datetime.fromisoformat(args.as_of).replace(tzinfo=UTC),
        )
    path = persist_snapshot(catalog, args.as_of, universe)
    return {
        "snapshot_date": args.as_of,
        "top_n": args.top_n,
        "method": args.method,
        "path": str(path),
        "coins": [u.coin for u in universe],
        "details": universe_as_dicts(universe),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    summary = asyncio.run(_main_async(args))
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
