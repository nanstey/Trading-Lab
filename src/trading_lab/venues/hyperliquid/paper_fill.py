"""
Hyperliquid paper-mode fill simulator.

`HyperliquidExecutionClient` in `is_paper=True` mode emits `OrderAccepted`
but otherwise no-ops — there's no real venue to confirm fills against.
This Actor closes that gap, mirroring `PolymarketPaperFillEngine`:

  1. Subscribes to live `OrderBookDeltas` for each instrument referenced
     by paper orders against HL.
  2. Maintains an in-memory best bid / best ask per instrument from
     incoming deltas (and trade ticks as a fallback for thin books).
  3. Holds pending paper orders registered by the execution client.
  4. On each book update, sweeps pending orders. Any whose limit price
     crosses the current touch fills at the touch price, emitting a real
     `OrderFilled` event via the message bus.

Conservative fill semantics (v1):
  - BUY fills only when `best_ask <= order.price`. Fill at `best_ask`.
  - SELL fills only when `best_bid >= order.price`. Fill at `best_bid`.
  - IOC orders fill at the first opportunity, otherwise `OrderCanceled`
    on the next book update.
  - Full-or-nothing fills (no partials).

Differences vs the Polymarket engine:
  - Notional is computed as `qty * price` in USDC (HL perps are linear).
  - Commission is `notional * taker_bps / 10_000` — HL charges takers
    in USDC against notional. PAPER without fees would silently inflate
    PnL vs the real venue.
  - `AccountId` defaults to `HYPERLIQUID-PAPER-001` (overridable by the
    runner).
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
    TimeInForce,
)
from nautilus_trader.model.events import OrderCanceled, OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    InstrumentId,
    PositionId,
    TradeId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity

log = logging.getLogger(__name__)


@dataclass
class _PendingOrder:
    order: Any
    submitted_book_seq: int
    cancelled: bool = False


class HyperliquidPaperFillConfig(ActorConfig, frozen=True):
    """Configuration for the HL paper-mode fill simulator."""

    component_id: str = "HYPERLIQUID-PAPER-FILL"
    # IOC orders cancel after this many book updates if they don't fill.
    ioc_max_book_updates: int = 1
    # Account currency — HL perps settle in USDC.
    account_currency: str = "USDC"
    # Taker fee in basis points (e.g. 4.5 = 0.045%). Applied to notional
    # for every paper fill. Source: config/portfolio.yaml:hyperliquid_fees.
    taker_bps: float = 4.5


class HyperliquidPaperFillEngine(Actor):
    """
    Paper-mode fill simulator for Hyperliquid.

    Lifecycle:
      `on_start`            — subscribes to deltas/ticks for any instrument
                              pre-registered via `register_instrument`.
      `register_pending`    — called by `HyperliquidExecutionClient` when an
                              order is accepted in paper mode.
      `on_order_book_deltas`/`on_trade_tick` — fills crossing orders or
                              cancels stale IOC orders.

    Emits `OrderFilled` / `OrderCanceled` via `self.msgbus.publish` on
    the same topic the real HL exec client would use.
    """

    def __init__(self, config: HyperliquidPaperFillConfig) -> None:
        super().__init__(config)
        self._cfg = config
        # iid_str → (best_bid, best_ask)
        self._touches: dict[str, tuple[float | None, float | None]] = {}
        # client_order_id_str → _PendingOrder
        self._pending: dict[str, _PendingOrder] = {}
        # iid_str → book update counter (IOC TTL)
        self._book_seq: dict[str, int] = {}
        # iid_str → subscribed flag
        self._subscribed: set[str] = set()
        # Set by the runner before start.
        self._account_id: AccountId | None = None
        # Pre-start instruments queued for subscription on `on_start`.
        self._instruments: list[InstrumentId] = []

    # ------------------------------------------------------------------
    # External API — called by runner / execution client
    # ------------------------------------------------------------------

    def set_account_id(self, account_id: AccountId) -> None:
        self._account_id = account_id

    def register_instrument(self, instrument_id: InstrumentId) -> None:
        if instrument_id not in self._instruments:
            self._instruments.append(instrument_id)

    def register_pending(self, order: Any) -> None:
        """Called by `HyperliquidExecutionClient` on paper-mode acceptance."""
        iid_str = str(order.instrument_id)
        self._pending[str(order.client_order_id)] = _PendingOrder(
            order=order,
            submitted_book_seq=self._book_seq.get(iid_str, 0),
        )
        if self.is_running and iid_str not in self._subscribed:
            self._subscribe(order.instrument_id)
        log.debug(
            "hl-paper-fill register cid=%s iid=%s side=%s qty=%s px=%s",
            order.client_order_id, iid_str, order.side,
            order.quantity, getattr(order, "price", None),
        )

    def cancel_pending(self, client_order_id_str: str) -> None:
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
        for cid in list(self._pending.keys()):
            self.cancel_pending(cid)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        iid_str = str(deltas.instrument_id)
        self._book_seq[iid_str] = self._book_seq.get(iid_str, 0) + 1
        best_bid, best_ask = self._touches.get(iid_str, (None, None))
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
        iid_str = str(tick.instrument_id)
        self._book_seq[iid_str] = self._book_seq.get(iid_str, 0) + 1
        px = float(tick.price)
        best_bid, best_ask = self._touches.get(iid_str, (None, None))
        if tick.aggressor_side == AggressorSide.BUYER:
            if best_ask is None or px <= best_ask:
                best_ask = px
        elif tick.aggressor_side == AggressorSide.SELLER:
            if best_bid is None or px >= best_bid:
                best_bid = px
        else:
            best_bid = max(best_bid or 0.0, px) if best_bid is not None else px
            best_ask = min(best_ask or float("inf"), px) if best_ask is not None else px
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

            if getattr(order, "time_in_force", None) == TimeInForce.IOC:
                seq = self._book_seq.get(iid_str, 0)
                if seq - po.submitted_book_seq >= max(1, self._cfg.ioc_max_book_updates):
                    self._emit_cancel(order)
                    self._pending.pop(cid_str, None)

    def _commission_usdc(self, fill_px: float, fill_qty: float) -> float:
        """Taker fee on notional. HL bills takers in USDC."""
        notional = fill_px * fill_qty
        return notional * (self._cfg.taker_bps / 10_000.0)

    def _emit_fill(self, order: Any, fill_px: float, fill_qty: float) -> None:
        try:
            from nautilus_trader.model.objects import Currency

            currency = Currency.from_str(self._cfg.account_currency)
            commission = self._commission_usdc(fill_px, fill_qty)
            event = OrderFilled(
                trader_id=order.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(f"PAPER-{order.client_order_id}"),
                account_id=self._account_id or AccountId("HYPERLIQUID-PAPER-001"),
                trade_id=TradeId(f"PAPER-T-{self._book_seq.get(str(order.instrument_id), 0)}"),
                position_id=PositionId(
                    f"{order.strategy_id}-{order.instrument_id.symbol}"
                ),
                order_side=order.side,
                order_type=order.order_type,
                last_qty=Quantity.from_str(f"{fill_qty:.6f}"),
                last_px=Price.from_str(f"{fill_px:.6f}"),
                currency=currency,
                commission=Money(commission, currency),
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
                "hl-paper-fill EMIT cid=%s px=%.6f qty=%.6f fee=%.6f",
                order.client_order_id, fill_px, fill_qty, commission,
            )
        except Exception as exc:
            log.warning("hl-paper-fill emit failed: %s", exc)

    def _emit_cancel(self, order: Any) -> None:
        try:
            event = OrderCanceled(
                trader_id=order.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(f"PAPER-{order.client_order_id}"),
                account_id=self._account_id or AccountId("HYPERLIQUID-PAPER-001"),
                ts_event=self.clock.timestamp_ns(),
                ts_init=self.clock.timestamp_ns(),
                reconciliation=False,
            )
            self.msgbus.publish(
                topic=f"events.order.{order.strategy_id}",
                msg=event,
            )
        except Exception as exc:
            log.warning("hl-paper-fill emit cancel failed: %s", exc)
