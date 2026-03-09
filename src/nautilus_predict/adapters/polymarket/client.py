"""
Polymarket CLOB REST and WebSocket Client.

Provides async access to all Polymarket CLOB API endpoints:
- Market data: markets, order books, trade history
- Order management: place, cancel, query orders
- Account: open positions, fills, balances
- WebSocket: real-time order book and user fill subscriptions

All REST methods use httpx.AsyncClient with automatic auth header injection.
WebSocket subscriptions use the websockets library.

API reference: https://docs.polymarket.com/#clob-api
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

import httpx

from nautilus_predict.adapters.polymarket.auth import PolymarketAuth
from nautilus_predict.config import PolymarketConfig

log = logging.getLogger(__name__)

# WebSocket channel types
MARKET_CHANNEL = "market"
USER_CHANNEL = "user"


class PolymarketClient:
    """
    Async HTTP and WebSocket client for the Polymarket CLOB API.

    Parameters
    ----------
    config : PolymarketConfig
        Polymarket connection configuration.
    auth : PolymarketAuth
        Authentication handler with L2 credentials configured.

    Example
    -------
    >>> client = PolymarketClient(config=poly_config, auth=auth)
    >>> markets = await client.get_markets()
    >>> book = await client.get_order_book(token_id="0xabc...")
    """

    def __init__(self, config: PolymarketConfig, auth: PolymarketAuth) -> None:
        self._config = config
        self._auth = auth
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> PolymarketClient:
        """Start the underlying HTTP client."""
        self._http = httpx.AsyncClient(
            base_url=self._config.host,
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
                "PolymarketClient must be used as an async context manager. "
                "Use: async with PolymarketClient(...) as client:"
            )
        return self._http

    # ------------------------------------------------------------------
    # Public market data (no auth required)
    # ------------------------------------------------------------------

    async def get_markets(self, next_cursor: str | None = None) -> dict[str, Any]:
        """
        Fetch a paginated list of active markets.

        Parameters
        ----------
        next_cursor : str, optional
            Pagination cursor from a previous response.

        Returns
        -------
        dict
            Paginated markets response with 'data' list and 'next_cursor'.
        """
        params: dict[str, str] = {}
        if next_cursor:
            params["next_cursor"] = next_cursor

        resp = await self._get_client().get("/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, condition_id: str) -> dict[str, Any]:
        """
        Fetch details for a single market.

        Parameters
        ----------
        condition_id : str
            Polymarket condition ID (0x-prefixed hex).

        Returns
        -------
        dict
            Market details including question, outcomes, active status.
        """
        resp = await self._get_client().get(f"/markets/{condition_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_order_book(self, token_id: str) -> dict[str, Any]:
        """
        Fetch the current order book snapshot for a token.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.

        Returns
        -------
        dict
            Order book with 'bids' and 'asks' price levels.
        """
        resp = await self._get_client().get("/book", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()

    async def get_fee_rate_bps(self, token_id: str) -> int:
        """
        Fetch the current maker fee rate in basis points for a token.

        Polymarket requires feeRateBps in every order payload for proper
        fee accounting. Most standard markets have zero maker fee + rebate.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.

        Returns
        -------
        int
            Fee rate in basis points (typically 0 for maker orders).

        TODO(live): Confirm correct endpoint path from Polymarket docs
        TODO(live): Cache this value - it changes infrequently
        """
        resp = await self._get_client().get("/neg-risk", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        return int(data.get("feeRateBps", 0))

    # ------------------------------------------------------------------
    # Authenticated order management
    # ------------------------------------------------------------------

    async def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        """
        Place a new order on the CLOB.

        The order dict must include feeRateBps for fee-aware quoting.
        Signs request with HMAC-SHA256 L2 credentials.

        Parameters
        ----------
        order : dict
            Order payload. Required fields:
            - token_id: str
            - side: "BUY" | "SELL"
            - price: str (decimal, e.g. "0.65")
            - size: str (decimal, e.g. "10.0")
            - type: "LIMIT" | "MARKET"
            - feeRateBps: int

        Returns
        -------
        dict
            Order confirmation with order_id and status.

        TODO(live): Wire to polyfill-rs for <100ms cancel/replace hot path
        """
        body = json.dumps(order)
        headers = self._auth.sign_request("POST", "/order", body)

        resp = await self._get_client().post(
            "/order",
            content=body,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()
        log.info(
            "Order placed",
            extra={
                "order_id": result.get("orderID"),
                "token_id": order.get("token_id"),
                "side": order.get("side"),
                "price": order.get("price"),
                "size": order.get("size"),
            },
        )
        return result

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """
        Cancel a specific open order.

        Parameters
        ----------
        order_id : str
            Order ID to cancel.

        Returns
        -------
        dict
            Cancellation confirmation.
        """
        body = json.dumps({"orderID": order_id})
        headers = self._auth.sign_request("DELETE", "/order", body)

        resp = await self._get_client().delete(
            "/order",
            content=body,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        log.info("Order cancelled", extra={"order_id": order_id})
        return resp.json()

    async def cancel_all_orders(self) -> dict[str, Any]:
        """
        Cancel ALL open orders across all markets.

        Used by the kill switch to halt all trading immediately.

        Returns
        -------
        dict
            Bulk cancellation result.
        """
        headers = self._auth.sign_request("DELETE", "/orders")

        resp = await self._get_client().delete(
            "/orders",
            headers=headers,
        )
        resp.raise_for_status()
        log.warning("All orders cancelled via bulk cancel")
        return resp.json()

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """
        Fetch all currently open orders.

        Returns
        -------
        list[dict]
            List of open order records.
        """
        path = "/orders"
        headers = self._auth.sign_request("GET", path)

        resp = await self._get_client().get(path, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def heartbeat(self) -> bool:
        """
        Send a heartbeat to keep WebSocket sessions alive.

        Polymarket drops user WebSocket connections if no heartbeat is
        received within the timeout period. This method pings the REST
        heartbeat endpoint as a fallback.

        Returns
        -------
        bool
            True if heartbeat was acknowledged, False otherwise.
        """
        try:
            headers = self._auth.sign_request("POST", "/heartbeat")
            resp = await self._get_client().post("/heartbeat", headers=headers)
            resp.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.RequestError) as exc:
            log.warning("Heartbeat failed", extra={"error": str(exc)})
            return False

    # ------------------------------------------------------------------
    # WebSocket subscriptions
    # ------------------------------------------------------------------

    async def subscribe_market(
        self,
        token_id: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """
        Subscribe to real-time order book updates for a market token.

        Messages are delivered to `callback` as they arrive via WebSocket.
        This method runs indefinitely until cancelled.

        Parameters
        ----------
        token_id : str
            Polymarket token ID to subscribe to.
        callback : async callable
            Coroutine to call with each incoming order book message.

        TODO(live): Implement reconnection with exponential backoff
        TODO(live): Handle subscription confirmation and error messages
        """
        import websockets

        subscribe_msg = {
            "auth": {},
            "type": MARKET_CHANNEL,
            "markets": [],
            "assets_ids": [token_id],
        }

        log.info("Connecting to market WebSocket", extra={"token_id": token_id})

        async with websockets.connect(self._config.ws_host) as ws:
            await ws.send(json.dumps(subscribe_msg))
            log.info("Market channel subscribed", extra={"token_id": token_id})

            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    await callback(msg)
                except Exception as exc:
                    log.error(
                        "Error processing market message",
                        extra={"error": str(exc), "token_id": token_id},
                    )

    async def subscribe_user(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """
        Subscribe to the user channel for real-time fill notifications.

        Receives events for: order fills, cancellations, and balance changes
        for the authenticated wallet.

        Parameters
        ----------
        callback : async callable
            Coroutine to call with each incoming user event message.

        TODO(live): Implement L2 authentication for user channel
        TODO(live): Implement reconnection with credential refresh
        """
        import websockets

        if self._auth.l2_credentials is None:
            raise RuntimeError("L2 credentials required for user channel subscription")

        creds = self._auth.l2_credentials
        auth_payload = {
            "apiKey": creds.api_key,
            "secret": creds.api_secret,
            "passphrase": creds.api_passphrase,
        }

        subscribe_msg = {
            "auth": auth_payload,
            "type": USER_CHANNEL,
            "markets": [],
            "assets_ids": [],
        }

        log.info("Connecting to user WebSocket", extra={"address": self._auth.address})

        async with websockets.connect(self._config.ws_host) as ws:
            await ws.send(json.dumps(subscribe_msg))
            log.info("User channel subscribed")

            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    await callback(msg)
                except Exception as exc:
                    log.error(
                        "Error processing user message",
                        extra={"error": str(exc)},
                    )
