#!/usr/bin/env python3
"""
Fetch and display active Polymarket markets.

Useful for discovering condition IDs to configure strategies and seeding
historical data downloads.

Usage:
    python scripts/fetch_markets.py
    python scripts/fetch_markets.py --limit 20 --keyword bitcoin
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List active Polymarket markets")
    parser.add_argument("--limit", type=int, default=10, help="Number of markets to display")
    parser.add_argument("--keyword", type=str, default=None, help="Filter by keyword in question")
    return parser.parse_args()


async def main() -> None:
    import os
    from nautilus_predict.venues.polymarket.auth import L2Credentials
    from nautilus_predict.venues.polymarket.client import PolymarketRestClient

    args = parse_args()

    creds = L2Credentials(
        api_key=os.environ.get("POLYMARKET_API_KEY", ""),
        api_secret=os.environ.get("POLYMARKET_API_SECRET", ""),
        api_passphrase=os.environ.get("POLYMARKET_API_PASSPHRASE", ""),
    )

    http_url = os.environ.get("POLYMARKET_HTTP_URL", "https://clob.polymarket.com")
    client = PolymarketRestClient(http_url=http_url, creds=creds)

    try:
        result = await client.get_markets()
        markets = result.get("data", [])

        if args.keyword:
            kw = args.keyword.lower()
            markets = [m for m in markets if kw in m.get("question", "").lower()]

        print(f"\nShowing {min(args.limit, len(markets))} of {len(markets)} markets:\n")
        for market in markets[: args.limit]:
            cid = market.get("condition_id", "")
            question = market.get("question", "N/A")
            tokens = market.get("tokens", [])
            yes_token = next((t for t in tokens if t.get("outcome") == "Yes"), {})
            no_token = next((t for t in tokens if t.get("outcome") == "No"), {})

            print(f"  Condition: {cid}")
            print(f"  Question:  {question}")
            print(f"  YES token: {yes_token.get('token_id', 'N/A')}")
            print(f"  NO token:  {no_token.get('token_id', 'N/A')}")
            print()
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
