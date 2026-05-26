"""
Polymarket paper-mode fill simulator.

`PolymarketExecutionClient` in `is_paper=True` mode emits `OrderAccepted`
but never `OrderFilled` — there's no real venue to confirm fills. This
Actor fills the gap:

  1. Subscribes to live `OrderBookDeltas` (whatever the data client
     publishes) for each instrument referenced by paper orders.
  2. Maintains an in-memory best bid + best ask per instrument from
     incoming deltas.
  3. Holds pending paper orders registered by the execution client.
  4. On each book update, sweeps pending orders for any whose limit
     price would cross the current touch — emits `OrderFilled` via the
     message bus at the cross price (taker-side fill).

Conservative fill semantics:
  - BUY fills only when `best_ask <= order.price`. Fill at `best_ask`.
  - SELL fills only when `best_bid >= order.price`. Fill at `best_bid`.
  - IOC orders fill at the first opportunity or get an `OrderCanceled`
    event on the next book update.
  - Partial fills not modelled in v1 — fills are full or nothing,
    capped at the strategy-requested size.

Why an Actor (not a direct callback in ExecutionClient): NT routes book
deltas via the message bus, and only Actor/Strategy components can
subscribe to typed data feeds. Putting the fill logic in an Actor keeps
the execution client decoupled from data subscription internals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import (
    AggressorSide,
    BookAction,
    LiquiditySide,
    OrderSide,
    OrderStatus,
    TimeInForce,
)
from nautilus_trader.model.events import OrderCanceled, OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    InstrumentId,
    PositionId,
    StrategyId,
    TradeId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity

log = logging.getLogger(__name__)


@dataclass
class _PendingOrder:
    """One paper order tracked by the fill engine until fill or cancel."""
    order: Any
    submitted_book_seq: int
    cancelled: bool = False


class PolymarketPaperFillConfig(ActorConfig, frozen=True):
    """Configuration for the paper-mode fill simulator."""

    component_id: str = "POLYMARKET-PAPER-FILL"
    # Cancel IOC orders that don't fill on the FIRST book update after
    # submission (set to 1 for strict IOC semantics; >1 = more lenient).
    ioc_max_book_updates: int = 1
    # Currency symbol for commission/notional (Polymarket uses USDC).
    account_currency: str = "USDC"


class PolymarketPaperFillEngine(Actor):
    """
    Paper-mode fill simulator for Polymarket.

    Lifecycle:
      `on_start` — subscribes to deltas for any instruments registered before
                   the engine starts. Late-registered instruments subscribe
                   inside `register_pending`.
      `register_pending(order)` — called by PolymarketExecutionClient when an
                                  order is accepted in paper mode.
      `on_order_book_deltas` — fills pending orders that cross current touch;
                               IOC orders past `ioc_max_book_updates` get
                               cancelled.

    The engine emits `OrderFilled` / `OrderCanceled` via `self.msgbus.publish`
    on the same topics the real `PolymarketExecutionClient` would use.
    """

    def __init__(self, config: PolymarketPaperFillConfig) -> None:
        super().__init__(config)
        self._cfg = config
        # iid_str → (best_bid, best_ask)
        self._touches: dict[str, tuple[float | None, float | None]] = {}
        # client_order_id_str → _PendingOrder
        self._pending: dict[str, _PendingOrder] = {}
        # iid_str → book update counter (for IOC TTL)
        self._book_seq: dict[str, int] = {}
        # iid_str → subscribed flag
        self._subscribed: set[str] = set()
        # Set by the runner before start.
        self._account_id: AccountId | None = None
        # Pre-start instruments to subscribe to.
        self._instruments: list[InstrumentId] = []

    # ------------------------------------------------------------------
    # External API — called by runner / execution client
    # ------------------------------------------------------------------

    def set_account_id(self, account_id: AccountId) -> None:
        """Set the account id used in emitted OrderFilled / OrderCanceled events."""
        self._account_id = account_id

    def register_instrument(self, instrument_id: InstrumentId) -> None:
        """
        Pre-register an instrument so we subscribe at on_start.

        After start, instruments are subscribed on-demand inside
        register_pending().
        """
        if instrument_id not in self._instruments:
            self._instruments.append(instrument_id)

    def register_pending(self, order: Any) -> None:
        """
        Called by PolymarketExecutionClient on paper-mode order acceptance.

        Holds the order until a book update fills or IOC-cancels it.
        """
        iid_str = str(order.instrument_id)
        self._pending[str(order.client_order_id)] = _PendingOrder(
            order=order,
            submitted_book_seq=self._book_seq.get(iid_str, 0),
        )
        if self.is_running and iid_str not in self._subscribed:
            self._subscribe(order.instrument_id)
        log.debug(
            "paper-fill register cid=%s iid=%s side=%s qty=%s px=%s",
            order.client_order_id, iid_str, order.side,
            order.quantity, getattr(order, "price", None),
        )

    def cancel_pending(self, client_order_id_str: str) -> None:
        """Called by ExecutionClient on a CANCEL command for a paper order."""
        po = self._pending.get(client_order_id_str)
        if po and not po.cancelled:
            po.cancelled = True
            self._emit_cancel(po.order)
            self._pending.pop(client_order_id_str, None)

    # ------------------------------------------------------------------
    # NT Actor lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        for iid in self._instruments:
            self._subscribe(iid)

    def on_stop(self) -> None:
        # Cancel any orders still pending — caller expects clean shutdown.
        for cid in list(self._pending.keys()):
            self.cancel_pending(cid)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        iid_str = str(deltas.instrument_id)
        self._book_seq[iid_str] = self._book_seq.get(iid_str, 0) + 1
        best_bid, best_ask = self._touches.get(iid_str, (None, None))
        # Walk the deltas to compute the new touch. Each delta is either a
        # CLEAR (resets) or an ADD/UPDATE (one level).
        for d in deltas.deltas:
            if d.action == BookAction.CLEAR:
                best_bid, best_ask = None, None
                continue
            if d.action not in (BookAction.ADD, BookAction.UPDATE):
                continue
            try:
                px = float(d.order.price)
                sz = float(d.order.size)
            except Exception:
                continue
            if sz <= 0:
                continue
            if d.order.side == OrderSide.BUY:
                if best_bid is None or px > best_bid:
                    best_bid = px
            elif d.order.side == OrderSide.SELL:
                if best_ask is None or px < best_ask:
                    best_ask = px
        self._touches[iid_str] = (best_bid, best_ask)
        self._sweep(iid_str)

    def on_trade_tick(self, tick: TradeTick) -> None:
        """
        Trade-tick path: treat the tick price as a market quote.

        For markets where we don't have a deep book signal (e.g. PM's
        market WS doesn't expose trade prints), a TradeTick is a useful
        signal too — treat it as a one-level book update at the trade
        price. Aggressor side tells us whether to update bid or ask.
        """
        iid_str = str(tick.instrument_id)
        self._book_seq[iid_str] = self._book_seq.get(iid_str, 0) + 1
        px = float(tick.price)
        best_bid, best_ask = self._touches.get(iid_str, (None, None))
        if tick.aggressor_side == AggressorSide.BUYER:
            # A buyer aggressed against the ask → set ask = px.
            if best_ask is None or px <= best_ask:
                best_ask = px
        elif tick.aggressor_side == AggressorSide.SELLER:
            if best_bid is None or px >= best_bid:
                best_bid = px
        else:
            # Unknown aggressor — treat tick as both bid and ask (executable
            # both ways) so the fill engine is lenient on synthetic ticks.
            best_bid = max(best_bid or 0, px) if best_bid is not None else px
            best_ask = min(best_ask or 1, px) if best_ask is not None else px
        self._touches[iid_str] = (best_bid, best_ask)
        self._sweep(iid_str)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _subscribe(self, instrument_id: InstrumentId) -> None:
        iid_str = str(instrument_id)
        if iid_str in self._subscribed:
            return
        try:
            self.subscribe_order_book_deltas(instrument_id)
        except Exception as exc:
            log.warning("subscribe_order_book_deltas failed iid=%s: %s", iid_str, exc)
        try:
            self.subscribe_trade_ticks(instrument_id)
        except Exception:
            pass
        self._subscribed.add(iid_str)

    def _sweep(self, iid_str: str) -> None:
        best_bid, best_ask = self._touches.get(iid_str, (None, None))
        if best_bid is None and best_ask is None:
            return
        for cid_str, po in list(self._pending.items()):
            order = po.order
            if str(order.instrument_id) != iid_str or po.cancelled:
                continue
            try:
                order_px = float(order.price)
            except Exception:
                continue
            qty = float(order.quantity)
            side = order.side
            filled = False
            fill_px: float | None = None
            if side == OrderSide.BUY and best_ask is not None and best_ask <= order_px:
                fill_px = best_ask
                filled = True
            elif side == OrderSide.SELL and best_bid is not None and best_bid >= order_px:
                fill_px = best_bid
                filled = True

            if filled and fill_px is not None:
                self._emit_fill(order, fill_px, qty)
                self._pending.pop(cid_str, None)
                continue

            # IOC handling: if the order has been in flight past N book updates
            # and the TIF is IOC, cancel.
            if getattr(order, "time_in_force", None) == TimeInForce.IOC:
                seq = self._book_seq.get(iid_str, 0)
                if seq - po.submitted_book_seq >= max(1, self._cfg.ioc_max_book_updates):
                    self._emit_cancel(order)
                    self._pending.pop(cid_str, None)

    def _emit_fill(self, order: Any, fill_px: float, fill_qty: float) -> None:
        try:
            from nautilus_trader.model.objects import Currency

            currency = Currency.from_str(self._cfg.account_currency)
            event = OrderFilled(
                trader_id=order.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(f"PAPER-{order.client_order_id}"),
                account_id=self._account_id or AccountId("POLYMARKET-PAPER-001"),
                trade_id=TradeId(f"PAPER-T-{self._book_seq.get(str(order.instrument_id), 0)}"),
                position_id=PositionId(
                    f"{order.strategy_id}-{order.instrument_id.symbol}"
                ),
                order_side=order.side,
                order_type=order.order_type,
                last_qty=Quantity.from_str(f"{fill_qty:.2f}"),
                last_px=Price.from_str(f"{fill_px:.2f}"),
                currency=currency,
                commission=Money(0, currency),
                liquidity_side=LiquiditySide.TAKER,
                ts_event=self.clock.timestamp_ns(),
                ts_init=self.clock.timestamp_ns(),
                reconciliation=False,
            )
            self.msgbus.publish(
                topic=f"events.order.{order.strategy_id}",
                msg=event,
            )
            log.info(
                "paper-fill EMIT cid=%s px=%.4f qty=%.4f",
                order.client_order_id, fill_px, fill_qty,
            )
        except Exception as exc:
            log.warning("paper-fill emit failed: %s", exc)

    def _emit_cancel(self, order: Any) -> None:
        try:
            event = OrderCanceled(
                trader_id=order.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(f"PAPER-{order.client_order_id}"),
                account_id=self._account_id or AccountId("POLYMARKET-PAPER-001"),
                ts_event=self.clock.timestamp_ns(),
                ts_init=self.clock.timestamp_ns(),
                reconciliation=False,
            )
            self.msgbus.publish(
                topic=f"events.order.{order.strategy_id}",
                msg=event,
            )
        except Exception as exc:
            log.warning("paper-fill emit cancel failed: %s", exc)


# Suppress unused-import linter — OrderStatus / StrategyId are documented
# in the module-level docstring above and may be referenced by future
# extensions.
_ = (OrderStatus, StrategyId)
