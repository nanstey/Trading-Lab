"""
Hyperliquid live execution client (NautilusTrader ExecutionClient).

Translates NautilusTrader Order commands into Hyperliquid API calls and
routes fill/cancel reports back onto the internal message bus.
"""

from __future__ import annotations

from typing import Any

import structlog
from nautilus_trader.execution.messages import CancelAllOrders, CancelOrder, SubmitOrder
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderRejected,
    OrderSubmitted,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    VenueOrderId,
)

from nautilus_predict.venues.hyperliquid.client import HyperliquidRestClient, HyperliquidWsClient

log = structlog.get_logger(__name__)

VENUE = "HYPERLIQUID"


class HyperliquidExecutionClient(LiveExecutionClient):
    """NautilusTrader execution client for Hyperliquid."""

    def __init__(
        self,
        loop,
        rest: HyperliquidRestClient,
        ws: HyperliquidWsClient,
        msgbus,
        cache,
        clock,
        is_paper: bool = False,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(VENUE),
            venue=None,
            oms_type=None,
            account_id=AccountId(f"{VENUE}-001"),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        self._rest = rest
        self._ws = ws
        self._is_paper = is_paper
        self._order_id_map: dict[str, int] = {}   # client_order_id → HL oid

    async def _connect(self) -> None:
        log.info("HyperliquidExecutionClient connecting", paper=self._is_paper)

    async def _disconnect(self) -> None:
        log.info("HyperliquidExecutionClient disconnecting")

    async def _submit_order(self, command: SubmitOrder) -> None:
        order = command.order
        log.info(
            "Submitting HL order",
            client_order_id=order.client_order_id,
            side=order.side,
            quantity=order.quantity,
        )

        self._send_order_submitted(order)

        if self._is_paper:
            self._send_order_accepted(order, VenueOrderId("PAPER-" + str(order.client_order_id)))
            return

        try:
            # Parse coin from instrument_id: "BTC-PERP.HYPERLIQUID" → "BTC"
            symbol = order.instrument_id.symbol.value
            coin = symbol.split("-")[0]

            result = await self._rest.place_order(
                coin=coin,
                is_buy=order.side == OrderSide.BUY,
                price=float(order.price),
                size=float(order.quantity),
            )

            # Extract order ID from response
            oid = self._extract_order_id(result)
            if oid is not None:
                self._order_id_map[str(order.client_order_id)] = oid
                self._send_order_accepted(order, VenueOrderId(str(oid)))
            else:
                self._send_order_rejected(order, reason=f"Unexpected response: {result}")

        except Exception as exc:
            log.error("HL order submission failed", error=str(exc))
            self._send_order_rejected(order, reason=str(exc))

    async def _cancel_order(self, command: CancelOrder) -> None:
        if self._is_paper:
            self._send_order_canceled(command)
            return

        client_id = str(command.client_order_id)
        oid = self._order_id_map.get(client_id)
        if oid is None:
            log.warning("HL cancel: no venue order ID found", client_order_id=client_id)
            return

        try:
            symbol = command.instrument_id.symbol.value
            coin = symbol.split("-")[0]
            await self._rest.cancel_order(coin, oid)
            self._send_order_canceled(command)
        except Exception as exc:
            log.error("HL cancel failed", oid=oid, error=str(exc))

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        if self._is_paper:
            return
        try:
            coin: str | None = None
            if command.instrument_id:
                coin = command.instrument_id.symbol.value.split("-")[0]
            await self._rest.cancel_all_orders(coin)
        except Exception as exc:
            log.error("HL cancel-all failed", error=str(exc))

    def handle_ws_message(self, msg: dict[str, Any]) -> None:
        channel = msg.get("channel")
        if channel == "orderUpdates":
            for update in msg.get("data", []):
                self._handle_order_update(update)
        elif channel == "userFills":
            for fill in msg.get("data", []):
                self._handle_fill(fill)

    def _handle_order_update(self, update: dict[str, Any]) -> None:
        log.debug("HL order update", oid=update.get("oid"), status=update.get("status"))

    def _handle_fill(self, fill: dict[str, Any]) -> None:
        log.debug("HL fill", tid=fill.get("tid"), coin=fill.get("coin"))

    @staticmethod
    def _extract_order_id(result: dict[str, Any]) -> int | None:
        try:
            statuses = result["response"]["data"]["statuses"]
            if statuses and "resting" in statuses[0]:
                return statuses[0]["resting"]["oid"]
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def _send_order_submitted(self, order) -> None:
        self._msgbus.publish(
            topic=f"events.order.{order.strategy_id}",
            msg=OrderSubmitted(
                trader_id=self.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                account_id=self.account_id,
                ts_event=self._clock.timestamp_ns(),
                ts_init=self._clock.timestamp_ns(),
            ),
        )

    def _send_order_accepted(self, order, venue_order_id: VenueOrderId) -> None:
        self._msgbus.publish(
            topic=f"events.order.{order.strategy_id}",
            msg=OrderAccepted(
                trader_id=self.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=venue_order_id,
                account_id=self.account_id,
                ts_event=self._clock.timestamp_ns(),
                ts_init=self._clock.timestamp_ns(),
                reconciliation=False,
            ),
        )

    def _send_order_rejected(self, order, reason: str) -> None:
        self._msgbus.publish(
            topic=f"events.order.{order.strategy_id}",
            msg=OrderRejected(
                trader_id=self.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                account_id=self.account_id,
                reason=reason,
                ts_event=self._clock.timestamp_ns(),
                ts_init=self._clock.timestamp_ns(),
            ),
        )

    def _send_order_canceled(self, command: CancelOrder) -> None:
        self._msgbus.publish(
            topic=f"events.order.{command.strategy_id}",
            msg=OrderCanceled(
                trader_id=self.trader_id,
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=None,
                account_id=self.account_id,
                ts_event=self._clock.timestamp_ns(),
                ts_init=self._clock.timestamp_ns(),
                reconciliation=False,
            ),
        )
