"""Hyperliquid public endpoint URLs.

Single source of truth for the REST/WS hosts. Importers should reference
these constants rather than re-typing the strings so a host change lands
in one place.
"""

from __future__ import annotations

MAINNET_HTTP_URL = "https://api.hyperliquid.xyz"
MAINNET_WS_URL = "wss://api.hyperliquid.xyz/ws"

TESTNET_HTTP_URL = "https://api.hyperliquid-testnet.xyz"
TESTNET_WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"
