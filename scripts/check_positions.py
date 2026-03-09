#!/usr/bin/env python3
"""
Display current positions on both Polymarket and Hyperliquid.

Usage:
    python scripts/check_positions.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()


async def main() -> None:
    import os

    from nautilus_predict.venues.hyperliquid.client import HyperliquidRestClient
    from nautilus_predict.venues.polymarket.auth import L2Credentials
    from nautilus_predict.venues.polymarket.client import PolymarketRestClient

    # Polymarket
    pm_creds = L2Credentials(
        api_key=os.environ.get("POLYMARKET_API_KEY", ""),
        api_secret=os.environ.get("POLYMARKET_API_SECRET", ""),
        api_passphrase=os.environ.get("POLYMARKET_API_PASSPHRASE", ""),
    )
    pm_client = PolymarketRestClient(
        http_url=os.environ.get("POLYMARKET_HTTP_URL", "https://clob.polymarket.com"),
        creds=pm_creds,
    )

    # Hyperliquid
    hl_client = HyperliquidRestClient(
        http_url=os.environ.get("HYPERLIQUID_HTTP_URL", "https://api.hyperliquid.xyz"),
        private_key=os.environ.get("HYPERLIQUID_PRIVATE_KEY", "0x" + "00" * 32),
        account_address=os.environ.get("HYPERLIQUID_ACCOUNT_ADDRESS", ""),
    )

    try:
        print("=== Polymarket Positions ===")
        pm_positions = await pm_client.get_positions()
        if pm_positions:
            for pos in pm_positions:
                print(f"  Token: {pos.get('asset', 'N/A')}")
                print(f"  Size:  {pos.get('size', 0)}")
                print(f"  Value: ${pos.get('value', 0):.4f}")
                print()
        else:
            print("  No positions.")

        print("\n=== Hyperliquid Positions ===")
        hl_positions = await hl_client.get_positions()
        if hl_positions:
            for pos in hl_positions:
                p = pos.get("position", {})
                print(f"  Coin:  {p.get('coin', 'N/A')}")
                print(f"  Size:  {p.get('szi', 0)}")
                print(f"  Entry: ${p.get('entryPx', 0)}")
                print(f"  PnL:   ${p.get('unrealizedPnl', 0):.4f}")
                print()
        else:
            print("  No positions.")

    finally:
        await pm_client.close()
        await hl_client.close()


if __name__ == "__main__":
    asyncio.run(main())
