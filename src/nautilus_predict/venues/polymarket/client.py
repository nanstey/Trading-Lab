"""
Polymarket CLOB REST and WebSocket client.

Wraps the raw HTTP and WebSocket APIs. Higher-level NautilusTrader adapter
modules import from here rather than touching aiohttp directly.

REST docs:   https://docs.polymarket.com/#rest-api
WS docs:     https://docs.polymarket.com/#websocket
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
import structlog

from nautilus_predict.venues.polymarket.auth import L2Credentials, sign_l2_request

log = structlog.get_logger(__name__)


class PolymarketRestClient:
    """Thin async REST client for the Polymarket CLOB API."""

    def __init__(
        self,
        http_url: str,
        creds: L2Credentials,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base = http_url.rstrip("/")
        self._creds = creds
        self._owned_session = session is None
        self._session: aiohttp.ClientSession = session or aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=32),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def close(self) -> None:
        if self._owned_session:
            await self._session.close()

    # ------------------------------------------------------------------
    # Public (unauthenticated) endpoints
    # ------------------------------------------------------------------

    async def get_markets(self, next_cursor: str = "") -> dict[str, Any]:
        """List active markets. Supports pagination via next_cursor."""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return await self._get("/markets", params=params, auth=False)

    async def get_market(self, condition_id: str) -> dict[str, Any]:
        """Fetch a single market by its condition ID."""
        return await self._get(f"/markets/{condition_id}", auth=False)

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """Fetch the current orderbook snapshot for a token (YES or NO share)."""
        return await self._get("/book", params={"token_id": token_id}, auth=False)

    async def get_last_trade_price(self, token_id: str) -> dict[str, Any]:
        """Fetch the last traded price for a token."""
        return await self._get("/last-trade-price", params={"token_id": token_id}, auth=False)

    # ------------------------------------------------------------------
    # Authenticated endpoints
    # ------------------------------------------------------------------

    async def get_orders(self, market: str | None = None) -> list[dict[str, Any]]:
        """List open orders, optionally filtered by market condition ID."""
        params = {}
        if market:
            params["market"] = market
        return await self._get("/orders", params=params, auth=True)

    async def create_order(self, order_payload: dict[str, Any]) -> dict[str, Any]:
        """
        Place a limit order.

        The order_payload must be a signed order struct; use
        polymarket.orders.build_order() to construct it correctly.
        """
        return await self._post("/order", body=order_payload, auth=True)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order by ID."""
        return await self._delete(f"/order/{order_id}", auth=True)

    async def cancel_all_orders(self, market: str | None = None) -> dict[str, Any]:
        """Cancel all open orders, optionally scoped to one market."""
        params = {}
        if market:
            params["market"] = market
        return await self._delete("/orders", params=params, auth=True)

    async def get_positions(self) -> list[dict[str, Any]]:
        """Fetch current token positions for the authenticated wallet."""
        return await self._get("/positions", auth=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        return sign_l2_request(self._creds, method, path, body).as_dict()

    async def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        auth: bool = False,
    ) -> Any:
        qs = ""
        if params:
            import urllib.parse
            qs = "?" + urllib.parse.urlencode(params)

        full_path = path + qs
        headers = self._auth_headers("GET", full_path) if auth else {}

        async with self._session.get(self._base + full_path, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        auth: bool = True,
    ) -> Any:
        body_str = json.dumps(body, separators=(",", ":"))
        headers = {"Content-Type": "application/json"}
        if auth:
            headers.update(self._auth_headers("POST", path, body_str))

        async with self._session.post(
            self._base + path,
            data=body_str,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _delete(
        self,
        path: str,
        params: dict[str, str] | None = None,
        auth: bool = True,
    ) -> Any:
        qs = ""
        if params:
            import urllib.parse
            qs = "?" + urllib.parse.urlencode(params)
        full_path = path + qs
        headers = self._auth_headers("DELETE", full_path) if auth else {}

        async with self._session.delete(self._base + full_path, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()


# ---------------------------------------------------------------------------
# WebSocket streaming
# ---------------------------------------------------------------------------


class PolymarketWsClient:
    """
    WebSocket client for Polymarket real-time channels.

    Supports two channel types:
    - "market"  : orderbook deltas, price updates (public)
    - "user"    : personal order updates (requires auth headers)

    Messages are delivered via an async callback.
    """

    RECONNECT_DELAY_S = 2.0
    MAX_RECONNECT_DELAY_S = 60.0

    def __init__(
        self,
        ws_url: str,
        creds: L2Credentials,
        on_message: Callable[[dict[str, Any]], None],
    ) -> None:
        self._ws_url = ws_url.rstrip("/")
        self._creds = creds
        self._on_message = on_message
        self._subscriptions: list[dict[str, Any]] = []
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def subscribe_market(self, assets_ids: list[str]) -> None:
        """Queue a subscription to market-channel events for the given token IDs."""
        self._subscriptions.append(
            {"auth": {}, "type": "market", "assets_ids": assets_ids}
        )

    def subscribe_user(self, markets: list[str]) -> None:
        """Queue a subscription to user-channel events (order updates)."""
        # User channel requires an auth token derived from L2 creds.
        auth_token = self._build_ws_auth_token()
        self._subscriptions.append(
            {"auth": auth_token, "type": "user", "markets": markets}
        )

    def _build_ws_auth_token(self) -> dict[str, str]:
        signed = sign_l2_request(self._creds, "GET", "/ws")
        return {
            "apiKey": signed.POLY_API_KEY,
            "secret": self._creds.api_secret,
            "passphrase": signed.POLY_PASSPHRASE,
        }

    async def connect_and_run(self) -> None:
        """Connect and stream messages until cancelled."""
        import websockets

        self._running = True
        delay = self.RECONNECT_DELAY_S

        while self._running:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    log.info("Polymarket WebSocket connected", url=self._ws_url)
                    delay = self.RECONNECT_DELAY_S  # reset on successful connect

                    # Send all queued subscriptions
                    for sub in self._subscriptions:
                        await ws.send(json.dumps(sub))

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            self._on_message(msg)
                        except json.JSONDecodeError:
                            log.warning("Polymarket WS: non-JSON message", raw=raw)

            except Exception as exc:
                if not self._running:
                    break
                log.warning(
                    "Polymarket WS disconnected, reconnecting",
                    error=str(exc),
                    delay=delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.MAX_RECONNECT_DELAY_S)

    def start(self) -> asyncio.Task[None]:
        self._task = asyncio.create_task(self.connect_and_run())
        return self._task

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
