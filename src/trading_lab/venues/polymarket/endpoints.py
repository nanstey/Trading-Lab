"""Polymarket public endpoint URLs and on-chain contract addresses.

Single source of truth for hosts/addresses. Importers should reference
these constants rather than re-typing the strings so a change lands in
one place.
"""

from __future__ import annotations

HTTP_URL = "https://clob.polymarket.com"
WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

# Polygon mainnet contracts.
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
