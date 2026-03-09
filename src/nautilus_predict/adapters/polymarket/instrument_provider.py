"""
Polymarket Instrument Provider.

Fetches active Polymarket markets and creates NautilusTrader Instrument
objects for each outcome token (YES and NO). These instruments are
registered in the NautilusTrader cache and used for order book
subscriptions, order submission, and position tracking.

Each Polymarket binary market generates two instruments:
- {condition_id}-YES: The YES outcome token
- {condition_id}-NO: The NO outcome token

TODO(live): Map Polymarket price increments to NautilusTrader Price type
TODO(live): Determine correct AccountType and OmsType for CLOB simulation
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_predict.adapters.polymarket.client import PolymarketClient
    from nautilus_predict.adapters.polymarket.data_types import PolymarketMarket

log = logging.getLogger(__name__)


class PolymarketInstrumentProvider:
    """
    Provides NautilusTrader instrument definitions for Polymarket markets.

    Fetches all active markets from the Polymarket CLOB and creates
    instrument objects that can be subscribed to and traded.

    Parameters
    ----------
    client : PolymarketClient
        Authenticated Polymarket client for fetching market data.

    Example
    -------
    >>> provider = PolymarketInstrumentProvider(client=poly_client)
    >>> await provider.load_all_async()
    >>> instruments = provider.list_all()
    """

    def __init__(self, client: PolymarketClient) -> None:
        self._client = client
        self._markets: dict[str, PolymarketMarket] = {}

    async def load_all_async(self) -> None:
        """
        Fetch all active markets and create instrument definitions.

        Paginates through all available markets, creating two instrument
        entries (YES and NO) for each active binary market.

        TODO(live): Create proper NautilusTrader BinaryOption or
        CryptoPerpetual instrument objects once NautilusTrader supports
        prediction market instrument types.
        """
        from nautilus_predict.adapters.polymarket.data_types import PolymarketMarket

        log.info("Loading all Polymarket instruments")

        cursor: str | None = None
        total_loaded = 0

        while True:
            response = await self._client.get_markets(next_cursor=cursor)
            markets_data = response.get("data", [])

            for raw_market in markets_data:
                if not raw_market.get("active", False) or raw_market.get("closed", True):
                    continue

                market = PolymarketMarket.from_api(raw_market)
                self._markets[market.condition_id] = market
                total_loaded += 1

            next_cursor = response.get("next_cursor")
            if not next_cursor or next_cursor == "LTE=":
                break
            cursor = next_cursor

        log.info("Instruments loaded", extra={"count": total_loaded})

    def list_all(self) -> list[PolymarketMarket]:
        """Return all loaded markets."""
        return list(self._markets.values())

    def get_market(self, condition_id: str) -> PolymarketMarket | None:
        """
        Return the market for a given condition ID, or None if not loaded.

        Parameters
        ----------
        condition_id : str
            Polymarket condition ID.
        """
        return self._markets.get(condition_id)

    def find_by_question(self, keyword: str) -> list[PolymarketMarket]:
        """
        Search for markets whose question contains a keyword.

        Parameters
        ----------
        keyword : str
            Case-insensitive search term.

        Returns
        -------
        list[PolymarketMarket]
            Matching markets.
        """
        keyword_lower = keyword.lower()
        return [
            m for m in self._markets.values()
            if keyword_lower in m.question.lower()
        ]

    def get_token_ids(self, condition_id: str) -> tuple[str, str] | None:
        """
        Return the (yes_token_id, no_token_id) pair for a market.

        Parameters
        ----------
        condition_id : str
            Polymarket condition ID.

        Returns
        -------
        tuple[str, str] or None
            (yes_token_id, no_token_id) or None if market not found.
        """
        market = self._markets.get(condition_id)
        if market is None:
            return None
        return market.yes_token_id, market.no_token_id
