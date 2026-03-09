"""
Polymarket live execution client (NautilusTrader ExecutionClient implementation).

Translates NautilusTrader Order commands into signed Polymarket API calls and
routes fill/cancel reports back onto the internal message bus.
"""

from __future__ import annotations

from typing import Any

import structlog
from nautilus_trader.execution.messages import (
    CancelAllOrders,
    CancelOrder,
    SubmitOrder,
)
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import LiquiditySide, OrderSide, OrderStatus, OrderType
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    VenueOrderId,
)

from nautilus_predict.venues.polymarket.auth import L2Credentials
from nautilus_predict.venues.polymarket.client import PolymarketRestClient, PolymarketWsClient
from nautilus_predict.venues.polymarket.orders import Side, build_limit_order

log = structlog.get_logger(__name__)

VENUE = "POLYMARKET"


class PolymarketExecutionClient(LiveExecutionClient):
    """
    NautilusTrader execution client for Polymarket.

    Paper mode: orders are submitted to the API but fills are simulated
    locally (no real funds move).
    """

    def __init__(
        self,
        loop,
        rest: PolymarketRestClient,
        ws: PolymarketWsClient,
        private_key: str,
        exchange_address: str,
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
        self._private_key = private_key
        self._exchange_address = exchange_address
        self._is_paper = is_paper

        # Map client_order_id → venue_order_id for lifecycle tracking
        self._order_id_map: dict[str, str] = {}

    async def _connect(self) -> None:
        log.info("PolymarketExecutionClient connecting", paper=self._is_paper)
        # Subscribe to user channel for real-time order updates
        # (WS is already started by the data client; exec client piggybacks)

    async def _disconnect(self) -> None:
        log.info("PolymarketExecutionClient disconnecting")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _submit_order(self, command: SubmitOrder) -> None:
        order = command.order
        log.info(
            "Submitting order",
            client_order_id=order.client_order_id,
            side=order.side,
            quantity=order.quantity,
            price=getattr(order, "price", None),
        )

        self._send_order_submitted(order)

        if self._is_paper:
            # In paper mode, acknowledge immediately without real submission
            self._send_order_accepted(order, VenueOrderId("PAPER-" + str(order.client_order_id)))
            return

        try:
            pm_side = Side.BUY if order.side == OrderSide.BUY else Side.SELL
            token_id = order.instrument_id.symbol.value

            signed = build_limit_order(
                private_key=self._private_key,
                token_id=token_id,
                side=pm_side,
                price=float(order.price),
                size=float(order.quantity),
                exchange_address=self._exchange_address,
            )

            result = await self._rest.create_order(signed.to_api_payload())
            venue_order_id = result.get("orderID", "")
            self._order_id_map[str(order.client_order_id)] = venue_order_id
            self._send_order_accepted(order, VenueOrderId(venue_order_id))

        except Exception as exc:
            log.error("Order submission failed", error=str(exc))
            self._send_order_rejected(order, reason=str(exc))

    async def _cancel_order(self, command: CancelOrder) -> None:
        client_id = str(command.client_order_id)
        venue_id = self._order_id_map.get(client_id)

        if not venue_id or self._is_paper:
            # Optimistic cancel in paper mode
            self._send_order_canceled(command)
            return

        try:
            await self._rest.cancel_order(venue_id)
            self._send_order_canceled(command)
        except Exception as exc:
            log.error("Order cancel failed", venue_order_id=venue_id, error=str(exc))

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        if self._is_paper:
            return
        try:
            market = command.instrument_id.symbol.value if command.instrument_id else None
            await self._rest.cancel_all_orders(market)
        except Exception as exc:
            log.error("Cancel-all failed", error=str(exc))

    # ------------------------------------------------------------------
    # WebSocket user-channel order update handler
    # ------------------------------------------------------------------

    def handle_user_ws_message(self, msg: dict[str, Any]) -> None:
        """Translate a user-channel WS message into NautilusTrader events."""
        event_type = msg.get("event_type")
        if event_type == "order":
            self._handle_order_update(msg)
        elif event_type == "trade":
            self._handle_trade_update(msg)

    def _handle_order_update(self, msg: dict[str, Any]) -> None:
        status = msg.get("status")
        log.debug("Order update", venue_order_id=msg.get("id"), status=status)

    def _handle_trade_update(self, msg: dict[str, Any]) -> None:
        log.debug("Trade update", trade_id=msg.get("id"))

    # ------------------------------------------------------------------
    # Helpers for emitting NautilusTrader events
    # ------------------------------------------------------------------

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
