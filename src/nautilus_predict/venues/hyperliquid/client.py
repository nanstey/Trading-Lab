"""
Hyperliquid REST and WebSocket client.

Hyperliquid's API is JSON-RPC style: all requests go to a single POST
endpoint (/info for reads, /exchange for writes). WebSocket subscriptions
follow a similar {"method": "subscribe", "subscription": {...}} pattern.

Reference: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import aiohttp
import structlog

from nautilus_predict.venues.hyperliquid.auth import (
    current_nonce,
    derive_address,
    sign_l1_action,
)

log = structlog.get_logger(__name__)


class HyperliquidRestClient:
    """Async REST client for the Hyperliquid API."""

    def __init__(
        self,
        http_url: str,
        private_key: str,
        account_address: str = "",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base = http_url.rstrip("/")
        self._private_key = private_key
        self._agent_address = derive_address(private_key)
        # vault_address: if set, routes trades through this account
        self._vault_address: str | None = account_address or None
        self._owned_session = session is None
        self._session: aiohttp.ClientSession = session or aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=16),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def close(self) -> None:
        if self._owned_session:
            await self._session.close()

    # ------------------------------------------------------------------
    # Info (read-only) endpoints
    # ------------------------------------------------------------------

    async def get_meta(self) -> dict[str, Any]:
        """Fetch exchange metadata (assets, lot sizes, etc.)."""
        return await self._info({"type": "meta"})

    async def get_all_mids(self) -> dict[str, str]:
        """Fetch mid prices for all spot/perp instruments."""
        return await self._info({"type": "allMids"})

    async def get_orderbook(self, coin: str, depth: int = 20) -> dict[str, Any]:
        """Fetch L2 orderbook for a given coin (e.g. "BTC")."""
        return await self._info({"type": "l2Book", "coin": coin, "nSigFigs": 5})

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """List open orders for the agent / vault address."""
        address = self._vault_address or self._agent_address
        return await self._info({"type": "openOrders", "user": address})

    async def get_positions(self) -> list[dict[str, Any]]:
        """Fetch perpetual positions for the account."""
        address = self._vault_address or self._agent_address
        result = await self._info({"type": "clearinghouseState", "user": address})
        return result.get("assetPositions", [])

    async def get_user_fills(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch recent fills for the account."""
        address = self._vault_address or self._agent_address
        return await self._info({"type": "userFills", "user": address})

    # ------------------------------------------------------------------
    # Exchange (write) endpoints
    # ------------------------------------------------------------------

    async def place_order(
        self,
        coin: str,
        is_buy: bool,
        price: float,
        size: float,
        order_type: dict[str, Any] | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """
        Place a limit or market order.

        Parameters
        ----------
        coin : str
            Asset name (e.g. "BTC", "ETH").
        is_buy : bool
            True for buy/long, False for sell/short.
        price : float
            Limit price (ignored for market orders).
        size : float
            Order size in base asset units.
        order_type : dict, optional
            {"limit": {"tif": "Gtc"}} or {"market": {}}. Defaults to GTC limit.
        reduce_only : bool
            True to only reduce existing position.
        """
        if order_type is None:
            order_type = {"limit": {"tif": "Gtc"}}

        action = {
            "type": "order",
            "orders": [
                {
                    "a": await self._coin_to_asset_index(coin),
                    "b": is_buy,
                    "p": self._format_price(price),
                    "s": self._format_size(size),
                    "r": reduce_only,
                    "t": order_type,
                }
            ],
            "grouping": "na",
        }
        return await self._exchange(action)

    async def cancel_order(self, coin: str, order_id: int) -> dict[str, Any]:
        """Cancel a specific order by its order ID."""
        action = {
            "type": "cancel",
            "cancels": [
                {
                    "a": await self._coin_to_asset_index(coin),
                    "o": order_id,
                }
            ],
        }
        return await self._exchange(action)

    async def cancel_all_orders(self, coin: str | None = None) -> dict[str, Any]:
        """Cancel all open orders, optionally filtered to one asset."""
        open_orders = await self.get_open_orders()
        cancels = []
        for order in open_orders:
            asset_idx = order.get("asset", 0)
            if coin is None or order.get("coin") == coin:
                cancels.append({"a": asset_idx, "o": order["oid"]})

        if not cancels:
            return {"status": "ok", "response": {"type": "cancel", "data": []}}

        action = {"type": "cancel", "cancels": cancels}
        return await self._exchange(action)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _meta_cache: dict[str, Any] | None = None

    async def _coin_to_asset_index(self, coin: str) -> int:
        if self._meta_cache is None:
            self._meta_cache = await self.get_meta()
        for i, asset in enumerate(self._meta_cache.get("universe", [])):
            if asset["name"] == coin:
                return i
        raise ValueError(f"Unknown Hyperliquid coin: {coin!r}")

    @staticmethod
    def _format_price(price: float) -> str:
        # Hyperliquid accepts up to 6 significant figures
        return f"{price:.6g}"

    @staticmethod
    def _format_size(size: float) -> str:
        return f"{size:.6g}"

    async def _info(self, payload: dict[str, Any]) -> Any:
        async with self._session.post(
            f"{self._base}/info",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _exchange(self, action: dict[str, Any]) -> Any:
        nonce = current_nonce()
        signature = sign_l1_action(self._private_key, action, self._vault_address, nonce)

        payload: dict[str, Any] = {
            "action": action,
            "nonce": nonce,
            "signature": {"r": signature[:66], "s": "0x" + signature[66:130], "v": int(signature[130:], 16)},
        }
        if self._vault_address:
            payload["vaultAddress"] = self._vault_address

        async with self._session.post(
            f"{self._base}/exchange",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


# ---------------------------------------------------------------------------
# WebSocket client
# ---------------------------------------------------------------------------


class HyperliquidWsClient:
    """
    Hyperliquid WebSocket streaming client.

    Supports subscriptions to:
    - l2Book       : orderbook deltas
    - trades       : public trade tape
    - orderUpdates : personal order lifecycle events
    - userFills    : personal fill events
    """

    RECONNECT_DELAY_S = 2.0
    MAX_RECONNECT_DELAY_S = 60.0

    def __init__(
        self,
        ws_url: str,
        on_message: Callable[[dict[str, Any]], None],
    ) -> None:
        self._ws_url = ws_url
        self._on_message = on_message
        self._subscriptions: list[dict[str, Any]] = []
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def subscribe_orderbook(self, coin: str) -> None:
        self._subscriptions.append({"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}})

    def subscribe_trades(self, coin: str) -> None:
        self._subscriptions.append({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}})

    def subscribe_order_updates(self, user: str) -> None:
        self._subscriptions.append(
            {"method": "subscribe", "subscription": {"type": "orderUpdates", "user": user}}
        )

    def subscribe_user_fills(self, user: str) -> None:
        self._subscriptions.append(
            {"method": "subscribe", "subscription": {"type": "userFills", "user": user}}
        )

    async def connect_and_run(self) -> None:
        import websockets

        self._running = True
        delay = self.RECONNECT_DELAY_S

        while self._running:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    log.info("Hyperliquid WebSocket connected")
                    delay = self.RECONNECT_DELAY_S

                    for sub in self._subscriptions:
                        await ws.send(json.dumps(sub))

                    # Send periodic ping to keep connection alive
                    ping_task = asyncio.create_task(self._ping_loop(ws))

                    try:
                        async for raw in ws:
                            if not self._running:
                                break
                            try:
                                msg = json.loads(raw)
                                self._on_message(msg)
                            except json.JSONDecodeError:
                                log.warning("HL WS: non-JSON message", raw=raw)
                    finally:
                        ping_task.cancel()

            except Exception as exc:
                if not self._running:
                    break
                log.warning("HL WS disconnected, reconnecting", error=str(exc), delay=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.MAX_RECONNECT_DELAY_S)

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send(json.dumps({"method": "ping"}))
            except Exception:
                break

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
