#!/usr/bin/env python3
"""
Derive Polymarket L2 API credentials from a wallet private key.

Run once to obtain API key/secret/passphrase, then store them in .env.
Never run this in production on an untrusted machine.

Usage:
    python scripts/derive_polymarket_keys.py

    # Or with explicit key:
    POLY_PRIVATE_KEY=0x... python scripts/derive_polymarket_keys.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add src to path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from trading_lab.venues.polymarket.auth import derive_address, derive_api_key
    from trading_lab.venues.polymarket.endpoints import HTTP_URL

    private_key = os.environ.get("POLY_PRIVATE_KEY")
    if not private_key:
        print("ERROR: POLY_PRIVATE_KEY not set in environment or .env file.")
        sys.exit(1)

    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    address = derive_address(private_key)
    print(f"Wallet address: {address}")
    print("Deriving L2 credentials via /auth/derive-api-key ...")

    http_url = os.environ.get("POLY_HTTP_URL", HTTP_URL)

    creds = await derive_api_key(
        http_url=http_url,
        private_key=private_key,
    )

    print("\n--- Add these to your .env file ---")
    print(f"POLY_API_KEY={creds.api_key}")
    print(f"POLY_API_SECRET={creds.api_secret}")
    print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
    print("------------------------------------")
    print("\nKeep these credentials secret. They grant full trading access.")


if __name__ == "__main__":
    asyncio.run(main())
