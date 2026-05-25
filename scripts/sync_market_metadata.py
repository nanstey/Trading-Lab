#!/usr/bin/env python3
"""
Sync Polymarket market metadata into the local MarketCatalog (sqlite).

Backed by gamma-api (public). Populates `data/market_catalog.db` so that
hypothesis-driven backtests can call `select_markets(criteria)` instead of
hand-picking condition IDs.

Usage:
    python scripts/sync_market_metadata.py --full        # all markets
    python scripts/sync_market_metadata.py               # incremental (default)
    python scripts/sync_market_metadata.py --active-only

Prints a JSON line on success.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

log = logging.getLogger("sync_markets")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--full", action="store_true", help="Sync ALL markets (active+closed+archived)")
    p.add_argument("--active-only", action="store_true", help="Skip closed/archived")
    p.add_argument(
        "--db",
        type=Path,
        default=Path("data/market_catalog.db"),
        help="MarketCatalog sqlite path",
    )
    p.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Page size (gamma caps at 100, default 100)",
    )
    p.add_argument("--max-pages", type=int, default=200)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    from nautilus_predict.data.market_catalog import MarketCatalog, gamma_to_row
    from nautilus_predict.venues.polymarket.gamma import GammaClient

    catalog = MarketCatalog(args.db)
    fetched_at = datetime.now(tz=UTC).isoformat()
    started = perf_counter()

    closed_filter: bool | None = None
    archived_filter: bool | None = None
    if args.active_only:
        closed_filter = False
        archived_filter = False
    elif not args.full:
        # Incremental default: active only too — full sync is opt-in.
        closed_filter = False

    fetched = 0
    upserted = 0
    async with GammaClient() as g:
        offset = 0
        for _ in range(args.max_pages):
            page = await g.get_markets(
                closed=closed_filter,
                archived=archived_filter,
                limit=args.page_size,
                offset=offset,
            )
            if not page:
                break
            fetched += len(page)
            rows = []
            for m in page:
                cid = m.get("conditionId") or ""
                if not cid:
                    continue
                # Gamma /markets doesn't always include the event slug — leave
                # as empty string. `series_slug` defaults to None when event
                # slug is missing; that's fine for v1.
                rows.append(gamma_to_row(m, fetched_at=fetched_at, event_slug=""))
            upserted += catalog.upsert_many(rows)
            if len(page) < args.page_size:
                break
            offset += args.page_size

    catalog.close()
    out = {
        "fetched": fetched,
        "upserted": upserted,
        "duration_sec": round(perf_counter() - started, 2),
        "db": str(args.db),
        "mode": "full" if args.full else ("active-only" if args.active_only else "incremental"),
    }
    print(json.dumps(out))
    return 0


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
