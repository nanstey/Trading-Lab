"""
Hyperliquid REST and WebSocket Client.

Used primarily as a real-time price oracle for Polymarket strategies and
as the execution venue for cross-venue hedge strategies.

Hyperliquid provides:
- Perpetual futures and spot markets
- Real-time WebSocket price feeds (used to inform Polymarket event pricing)
- Order placement via signed JSON payloads (Ethereum signature)

API reference: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Callable, Awaitable

import httpx

from nautilus_predict.config import HyperliquidConfig

log = logging.getLogger(__name__)


class HyperliquidClient:
    """
    Async HTTP and WebSocket client for the Hyperliquid API.

    Used primarily as a price oracle for Polymarket strategies.
    Also supports order placement for cross-venue hedge strategies.

    Parameters
    ----------
    config : HyperliquidConfig
        Hyperliquid connection configuration.

    Example
    -------
    >>> client = HyperliquidClient(config=hl_config)
    >>> price = await client.get_price("BTC")
    >>> await client.subscribe_prices(["BTC", "ETH"], my_callback)
    """

    def __init__(self, config: HyperliquidConfig) -> None:
        self._config = config
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HyperliquidClient:
        """Start the underlying HTTP client."""
        self._http = httpx.AsyncClient(
            base_url=self._config.api_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None:
            await self._http.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        """Return the active HTTP client, raising if not started."""
        if self._http is None:
            raise RuntimeError(
                "HyperliquidClient must be used as an async context manager."
            )
        return self._http

    async def _post_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        POST to the Hyperliquid /info endpoint (public, no auth).

        Parameters
        ----------
        payload : dict
            Request payload with 'type' and optional fields.
        """
        resp = await self._get_client().post("/info", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Price data (primary use case: price oracle)
    # ------------------------------------------------------------------

    async def get_price(self, coin: str) -> Decimal:
        """
        Fetch the current mid-price for a spot or perpetual market.

        This is the primary method used by Polymarket strategies to
        obtain the underlying asset price for event probability estimation.

        Parameters
        ----------
        coin : str
            Hyperliquid coin symbol (e.g., "BTC", "ETH", "SOL").

        Returns
        -------
        Decimal
            Current mid-price in USD.

        Raises
        ------
        ValueError
            If the coin is not found in the response.
        """
        data = await self._post_info({"type": "allMids"})

        # Response is a dict mapping coin -> mid price string
        if coin not in data:
            raise ValueError(f"Coin '{coin}' not found in Hyperliquid allMids response")

        return Decimal(str(data[coin]))

    async def get_all_mids(self) -> dict[str, Decimal]:
        """
        Fetch mid-prices for all available perpetual markets.

        Returns
        -------
        dict[str, Decimal]
            Mapping of coin symbol to mid-price.
        """
        data = await self._post_info({"type": "allMids"})
        return {coin: Decimal(str(price)) for coin, price in data.items()}

    async def get_orderbook(self, coin: str) -> dict[str, Any]:
        """
        Fetch the current order book for a coin's perpetual market.

        Parameters
        ----------
        coin : str
            Hyperliquid coin symbol.

        Returns
        -------
        dict
            Order book with 'levels' list containing bid/ask price levels.
        """
        data = await self._post_info({"type": "l2Book", "coin": coin})
        return data

    async def get_meta(self) -> dict[str, Any]:
        """
        Fetch metadata for all perpetual markets.

        Returns
        -------
        dict
            Universe metadata including available coins and trading parameters.
        """
        return await self._post_info({"type": "meta"})

    # ------------------------------------------------------------------
    # Order placement (for cross-venue hedge strategy)
    # ------------------------------------------------------------------

    async def place_order(
        self,
        coin: str,
        is_buy: bool,
        price: Decimal,
        size: Decimal,
        reduce_only: bool = False,
        order_type: str = "Limit",
        time_in_force: str = "Gtc",
    ) -> dict[str, Any]:
        """
        Place an order on Hyperliquid perpetuals.

        Requires a valid private key in HyperliquidConfig for signing.
        Orders are signed with Ethereum private key as per Hyperliquid spec.

        Parameters
        ----------
        coin : str
            Coin symbol to trade.
        is_buy : bool
            True for buy/long, False for sell/short.
        price : Decimal
            Limit price in USD.
        size : Decimal
            Order size in coin units.
        reduce_only : bool
            If True, order can only reduce an existing position.
        order_type : str
            "Limit" or "Market".
        time_in_force : str
            "Gtc" (Good Till Cancelled), "Ioc" (Immediate or Cancel),
            or "Alo" (Add Liquidity Only / post-only).

        Returns
        -------
        dict
            Order placement response with status and order ID.

        TODO(live): Implement Hyperliquid-specific EIP-712 order signing
        TODO(live): Determine correct coin index from meta response
        """
        if not self._config.has_credentials:
            raise RuntimeError(
                "Hyperliquid private key not configured. "
                "Set HL_PRIVATE_KEY in environment to place orders."
            )

        log.info(
            "Placing Hyperliquid order",
            extra={
                "coin": coin,
                "is_buy": is_buy,
                "price": float(price),
                "size": float(size),
            },
        )

        # TODO(live): Build and sign the order payload per Hyperliquid spec
        # See: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint
        raise NotImplementedError(
            "Hyperliquid order placement requires EIP-712 signing implementation. "
            "See TODO(live) markers in this method."
        )

    # ------------------------------------------------------------------
    # WebSocket subscriptions
    # ------------------------------------------------------------------

    async def subscribe_prices(
        self,
        coins: list[str],
        callback: Callable[[str, Decimal], Awaitable[None]],
    ) -> None:
        """
        Subscribe to real-time mid-price updates for multiple coins.

        This is the primary data feed used by catalyst and hedge strategies.
        Delivers price ticks via callback as they arrive.

        Parameters
        ----------
        coins : list[str]
            List of coin symbols to subscribe to.
        callback : async callable
            Coroutine called with (coin: str, price: Decimal) on each tick.

        TODO(live): Implement reconnection with exponential backoff
        TODO(live): Handle subscription heartbeat to detect stale feeds
        """
        import websockets

        subscribe_msg = {
            "method": "subscribe",
            "subscription": {"type": "allMids"},
        }

        log.info("Connecting to Hyperliquid price WebSocket", extra={"coins": coins})
        coins_set = set(coins)

        async with websockets.connect(self._config.ws_url) as ws:
            await ws.send(json.dumps(subscribe_msg))
            log.info("Hyperliquid price feed subscribed")

            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    if msg.get("channel") == "allMids" and "data" in msg:
                        mids = msg["data"].get("mids", {})
                        for coin, price_str in mids.items():
                            if coin in coins_set:
                                await callback(coin, Decimal(str(price_str)))
                except Exception as exc:
                    log.error(
                        "Error processing Hyperliquid price message",
                        extra={"error": str(exc)},
                    )

    async def subscribe_orderbook(
        self,
        coin: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """
        Subscribe to real-time order book updates for a single coin.

        Parameters
        ----------
        coin : str
            Coin symbol to subscribe to.
        callback : async callable
            Coroutine called with the raw order book message.

        TODO(live): Implement reconnection logic
        """
        import websockets

        subscribe_msg = {
            "method": "subscribe",
            "subscription": {"type": "l2Book", "coin": coin},
        }

        async with websockets.connect(self._config.ws_url) as ws:
            await ws.send(json.dumps(subscribe_msg))
            log.info("Hyperliquid order book subscribed", extra={"coin": coin})

            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    await callback(msg)
                except Exception as exc:
                    log.error(
                        "Error processing HL order book message",
                        extra={"error": str(exc), "coin": coin},
                    )
