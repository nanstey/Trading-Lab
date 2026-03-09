"""
Hyperliquid Instrument Provider.

Fetches available perpetual markets from Hyperliquid and creates
NautilusTrader instrument definitions for use in cross-venue strategies.

Hyperliquid instruments are used as price oracles and hedging venues
for Polymarket event strategies.

TODO(live): Map Hyperliquid perpetual specs to NautilusTrader CryptoPerpetual
TODO(live): Fetch and cache contract sizes, tick sizes, and margin requirements
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_predict.adapters.hyperliquid.client import HyperliquidClient

log = logging.getLogger(__name__)


class HyperliquidInstrumentProvider:
    """
    Provides NautilusTrader instrument definitions for Hyperliquid markets.

    Parameters
    ----------
    client : HyperliquidClient
        Authenticated Hyperliquid client.

    Example
    -------
    >>> provider = HyperliquidInstrumentProvider(client=hl_client)
    >>> await provider.load_all_async()
    >>> coins = provider.list_coins()
    """

    def __init__(self, client: HyperliquidClient) -> None:
        self._client = client
        self._meta: dict = {}
        self._coins: list[str] = []

    async def load_all_async(self) -> None:
        """
        Fetch market metadata and build instrument list.

        Fetches the /info meta endpoint to get all available perpetual
        markets, their contract sizes, and trading parameters.

        TODO(live): Create proper NautilusTrader CryptoPerpetual instruments
        """
        log.info("Loading Hyperliquid instruments")
        self._meta = await self._client.get_meta()

        universe = self._meta.get("universe", [])
        self._coins = [coin_meta.get("name", "") for coin_meta in universe]

        log.info("Hyperliquid instruments loaded", extra={"count": len(self._coins)})

    def list_coins(self) -> list[str]:
        """Return all available coin symbols."""
        return list(self._coins)

    def get_coin_index(self, coin: str) -> int | None:
        """
        Return the integer index of a coin in the universe.

        Hyperliquid uses integer indices for order payload construction.

        Parameters
        ----------
        coin : str
            Coin symbol (e.g., "BTC").

        Returns
        -------
        int or None
            Coin index or None if not found.
        """
        try:
            return self._coins.index(coin)
        except ValueError:
            return None

    def get_meta(self, coin: str) -> dict | None:
        """
        Return metadata for a specific coin.

        Parameters
        ----------
        coin : str
            Coin symbol.

        Returns
        -------
        dict or None
            Coin metadata including szDecimals, maxLeverage, etc.
        """
        universe = self._meta.get("universe", [])
        for entry in universe:
            if entry.get("name") == coin:
                return entry
        return None
