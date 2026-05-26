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
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderRejected,
    OrderSubmitted,
)
from nautilus_trader.model.identifiers import (
    ClientId,
    VenueOrderId,
)

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
        instrument_provider=None,
    ) -> None:
        from nautilus_trader.model.currencies import USDC
        from nautilus_trader.model.enums import AccountType, OmsType
        from nautilus_trader.model.identifiers import Venue

        if instrument_provider is None:
            from nautilus_trader.common.providers import InstrumentProvider
            from nautilus_trader.config import InstrumentProviderConfig
            instrument_provider = InstrumentProvider(
                config=InstrumentProviderConfig(),
            )
        super().__init__(
            loop=loop,
            client_id=ClientId(VENUE),
            venue=Venue(VENUE),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=USDC,
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        # NT's ExecutionClient derives `account_id` from client_id; we
        # don't need a separate AccountId attribute. The base class
        # exposes `self.account_id`.
        self._rest = rest
        self._ws = ws
        self._private_key = private_key
        self._exchange_address = exchange_address
        self._is_paper = is_paper

        # Map client_order_id → venue_order_id for lifecycle tracking
        self._order_id_map: dict[str, str] = {}

        # Paper-mode: a `PolymarketPaperFillEngine` actor is registered with
        # the trading node and we delegate fill simulation to it. The runner
        # sets this attribute after constructing both components.
        self._paper_fill_engine: Any = None

        # Pre-trade capital-cap gate. Runner constructs a `PortfolioAllocator`
        # per slug and assigns it here after `node.build()`. None = no gate
        # (legacy / tests). See `nautilus_predict.agent.portfolio`.
        self._portfolio_allocator: Any = None

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

        # Pre-trade capital-cap gate. If the allocator is wired and rejects
        # this order, we publish an OrderRejected (so NT's state machine
        # tracks it as a terminal-state order rather than a pending one)
        # and emit a `portfolio_alloc_breach` event for the operator agent.
        if self._portfolio_allocator is not None:
            try:
                decision = self._portfolio_allocator.check_order(order)
            except Exception as exc:
                log.warning("allocator.check_order raised; failing open: %s", exc)
                decision = None
            if decision is not None and not decision.accepted:
                self._send_order_rejected(order, reason=decision.reason)
                try:
                    from nautilus_predict.agent.events import emit_event

                    emit_event(
                        type="portfolio_alloc_breach",
                        summary=(
                            f"{self._portfolio_allocator.slug}: order rejected — "
                            f"{decision.reason}"
                        ),
                        severity="warn",
                        slug=self._portfolio_allocator.slug,
                        data={
                            "instrument_id": str(order.instrument_id),
                            "side": str(order.side),
                            "qty": float(order.quantity),
                            "price": float(order.price),
                            "proposed_notional_usdc": decision.proposed_notional_usdc,
                            "open_notional_before": decision.open_notional_before,
                            "open_notional_after": decision.open_notional_after,
                            "cap_usdc": decision.cap_usdc,
                            "is_paper": self._is_paper,
                        },
                    )
                except Exception as exc:
                    log.debug("emit_event failed (non-fatal): %s", exc)
                return

        if self._is_paper:
            # Paper mode — acknowledge immediately and hand the order to the
            # paper-fill engine. The engine watches live book updates and
            # emits OrderFilled / OrderCanceled events via the same msgbus
            # topic the real venue path uses, so the strategy can't tell
            # paper from live just by event flow.
            self._send_order_accepted(
                order, VenueOrderId("PAPER-" + str(order.client_order_id))
            )
            if self._paper_fill_engine is not None:
                try:
                    self._paper_fill_engine.register_pending(order)
                except Exception as exc:
                    log.warning("paper-fill register failed", error=str(exc))
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

        if self._is_paper:
            if self._paper_fill_engine is not None:
                self._paper_fill_engine.cancel_pending(client_id)
            else:
                self._send_order_canceled(command)
            return
        if not venue_id:
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
        """
        Parse Polymarket user-channel order-status updates.

        Polymarket status values mapped here:
          - LIVE / OPEN       → OrderAccepted (acknowledged but not filled)
          - MATCHED / FILLED  → OrderFilled (delegate to _handle_trade_update
                                 for the size/price detail)
          - CANCELED          → OrderCanceled
          - REJECTED          → OrderRejected
        """
        status = (msg.get("status") or "").upper()
        venue_order_id = str(msg.get("id") or msg.get("order_id") or "")
        client_order_id = self._client_id_for_venue(venue_order_id)
        log.info(
            "user-channel order update",
            venue_order_id=venue_order_id,
            status=status,
        )
        if status in ("LIVE", "OPEN", "ACCEPTED"):
            order = self._cached_order(client_order_id)
            if order is not None and venue_order_id:
                from nautilus_trader.model.identifiers import VenueOrderId as _VOID
                self._send_order_accepted(order, _VOID(venue_order_id))
        elif status in ("MATCHED", "FILLED"):
            # Trade detail comes in the companion trade message; if this is
            # the only message we get, synthesise a fill from `size_matched`
            # and `price` if present.
            self._handle_trade_update(msg)
        elif status == "CANCELED":
            self._emit_cancel(client_order_id, msg.get("instrument_id"))
        elif status == "REJECTED":
            order = self._cached_order(client_order_id)
            if order is not None:
                self._send_order_rejected(order, reason=msg.get("reason", "venue rejected"))

    def _handle_trade_update(self, msg: dict[str, Any]) -> None:
        """
        Translate a Polymarket trade confirmation into `OrderFilled`.

        Required fields on the inbound message (per PM user-channel docs):
          - id              (venue trade id)
          - order_id        (venue order id)
          - size_matched    (filled quantity)
          - price           (fill price)
          - taker_order_id  (optional — set if we're the taker)
        """
        venue_trade_id = str(msg.get("id") or "")
        venue_order_id = str(msg.get("order_id") or "")
        client_order_id = self._client_id_for_venue(venue_order_id)
        order = self._cached_order(client_order_id)
        if order is None:
            log.warning(
                "trade for unknown order — skipping",
                venue_order_id=venue_order_id,
                client_order_id=client_order_id,
            )
            return
        try:
            qty = float(msg.get("size_matched") or msg.get("size") or 0)
            price = float(msg.get("price") or 0)
        except (TypeError, ValueError):
            log.warning("trade update missing numeric size/price", msg=msg)
            return
        if qty <= 0 or price <= 0:
            return
        self._send_order_filled(
            order=order,
            venue_order_id=venue_order_id,
            venue_trade_id=venue_trade_id,
            last_qty=qty,
            last_px=price,
        )

    # ------------------------------------------------------------------
    # Helper: cache lookups for order/client-id mapping
    # ------------------------------------------------------------------

    def _client_id_for_venue(self, venue_order_id: str) -> str:
        for client_id, vid in self._order_id_map.items():
            if vid == venue_order_id:
                return client_id
        return ""

    def _cached_order(self, client_order_id: str):
        if not client_order_id:
            return None
        from nautilus_trader.model.identifiers import ClientOrderId
        try:
            return self._cache.order(ClientOrderId(client_order_id))
        except Exception:
            return None

    def _emit_cancel(self, client_order_id: str, instrument_id_str) -> None:
        """Emit OrderCanceled when the venue tells us a previously-known order is gone."""
        from nautilus_trader.model.events import OrderCanceled as _OC
        from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
        order = self._cached_order(client_order_id)
        instrument_id = order.instrument_id if order is not None else (
            InstrumentId.from_str(instrument_id_str) if instrument_id_str else None
        )
        if instrument_id is None:
            return
        strategy_id = order.strategy_id if order is not None else None
        self._msgbus.publish(
            topic=f"events.order.{strategy_id}",
            msg=_OC(
                trader_id=self.trader_id,
                strategy_id=strategy_id,
                instrument_id=instrument_id,
                client_order_id=ClientOrderId(client_order_id),
                venue_order_id=None,
                account_id=self.account_id,
                ts_event=self._clock.timestamp_ns(),
                ts_init=self._clock.timestamp_ns(),
                reconciliation=False,
            ),
        )

    def _send_order_filled(
        self,
        order,
        venue_order_id: str,
        venue_trade_id: str,
        last_qty: float,
        last_px: float,
    ) -> None:
        """Publish an OrderFilled event for the given order."""
        from nautilus_trader.model.enums import LiquiditySide
        from nautilus_trader.model.events import OrderFilled
        from nautilus_trader.model.identifiers import (
            PositionId,
            TradeId,
        )
        from nautilus_trader.model.identifiers import (
            VenueOrderId as _VOID,
        )
        from nautilus_trader.model.objects import Money, Price, Quantity

        # Polymarket commission is zero on most binary markets currently.
        # Position id derives from the (strategy, instrument) tuple via cache.
        self._msgbus.publish(
            topic=f"events.order.{order.strategy_id}",
            msg=OrderFilled(
                trader_id=self.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=_VOID(venue_order_id),
                account_id=self.account_id,
                trade_id=TradeId(venue_trade_id[:32] or f"T{self._clock.timestamp_ns()}"),
                position_id=PositionId(f"{order.strategy_id}-{order.instrument_id.symbol}"),
                order_side=order.side,
                order_type=order.order_type,
                last_qty=Quantity.from_str(f"{last_qty:.2f}"),
                last_px=Price.from_str(f"{last_px:.2f}"),
                currency=order.instrument_id.symbol,  # placeholder; venue should provide
                commission=Money(0, order.instrument_id.symbol),  # placeholder
                liquidity_side=LiquiditySide.TAKER,
                ts_event=self._clock.timestamp_ns(),
                ts_init=self._clock.timestamp_ns(),
                reconciliation=False,
            ),
        )

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
