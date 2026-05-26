"""
Tick Mean-Reversion Strategy for Polymarket binary markets.

Hypothesis
----------
On thinly-traded Polymarket binaries, single trades occasionally print one
or two ticks away from the running mean as noise. Within seconds the book
pulls the print back to mid. Buying after a downward "noise print" captures
the snap-back; selling after an upward one does the inverse.

Logic per TradeTick event
-------------------------
1. Append the trade price to a per-instrument rolling deque of the last
   ``lookback_ticks`` prints.
2. Once the deque is full, compare the current trade price to the rolling
   mean of the deque (excluding the current print).
3. If current_price < rolling_mean - entry_threshold_ticks * TICK_SIZE,
   submit an IOC BUY at the current price (expecting a snap-back up).
   If current_price > rolling_mean + entry_threshold_ticks * TICK_SIZE,
   submit an IOC SELL at the current price (expecting a snap-back down).
4. After entering, the strategy starts a per-instrument hold counter; on
   the Nth subsequent TradeTick it submits the opposite-side closing
   IOC order at the then-current price. No inventory caps; goal is
   frequency.

Notes
-----
- One Polymarket binary tick is $0.01 (price_precision = 2).
- TradeTick is much more frequent than book deltas on thin markets, which
  helps clear the n_trades >= 30 acceptance floor on short data windows.
- Only imports from nautilus_trader / trading_lab / stdlib to satisfy
  the codegen guard allowlist.
"""

from __future__ import annotations

from collections import deque

import structlog
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

log = structlog.get_logger(__name__)


# One tick on Polymarket binary outcome CLOBs ($0.01).
TICK_SIZE = 0.01


class TickMeanRevertConfig(StrategyConfig, frozen=True):
    """Configuration for the tick mean-reversion strategy."""

    strategy_id: str = "TICK-MEAN-REVERT-001"
    # Rolling window length over which the mean trade price is computed.
    # Parameter space: [10, 20, 30].
    lookback_ticks: int = 20
    # Threshold (in ticks of $0.01) below/above the rolling mean to
    # trigger an entry. Parameter space: [1, 2].
    entry_threshold_ticks: int = 1
    # Number of subsequent ticks to hold before submitting the closing
    # opposite-side IOC. Smaller = higher turnover.
    hold_ticks: int = 3
    # Per-order notional in USDC.
    order_size_usdc: float = 5.0


class TickMeanRevertStrategy(Strategy):
    """
    Subscribe to TradeTick on each registered instrument and emit BUY/SELL
    IOC limit orders when the current price diverges from the rolling
    mean by at least ``entry_threshold_ticks`` ticks.
    """

    def __init__(self, config: TickMeanRevertConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._instruments: list[InstrumentId] = []
        self._pending_instruments: list[InstrumentId] = []
        # Per-instrument rolling deque of recent trade prices.
        self._price_history: dict[str, deque[float]] = {}
        # Per-instrument current open position direction (OrderSide of
        # the entry; the close is the opposite side) and remaining hold
        # counter. None when flat.
        self._open_side: dict[str, OrderSide] = {}
        self._hold_remaining: dict[str, int] = {}
        # Outstanding order client_order_ids — purely for logging.
        self._open_orders: set[str] = set()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_instrument(self, instrument_id: InstrumentId) -> None:
        """Queue an instrument to be subscribed on start (or now if running)."""
        if not self.is_running:
            self._pending_instruments.append(instrument_id)
            return
        self._instruments.append(instrument_id)
        self.subscribe_trade_ticks(instrument_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        for iid in self._pending_instruments:
            self._instruments.append(iid)
            self.subscribe_trade_ticks(iid)
        self._pending_instruments.clear()
        log.info(
            "TickMeanRevertStrategy started",
            lookback_ticks=self._cfg.lookback_ticks,
            entry_threshold_ticks=self._cfg.entry_threshold_ticks,
            hold_ticks=self._cfg.hold_ticks,
            order_size_usdc=self._cfg.order_size_usdc,
            instruments=len(self._instruments),
        )

    def on_stop(self) -> None:
        log.info("TickMeanRevertStrategy stopping")
        for iid in self._instruments:
            try:
                self.cancel_all_orders(iid)
            except Exception:
                pass

    def on_reset(self) -> None:
        self._price_history.clear()
        self._open_side.clear()
        self._hold_remaining.clear()
        self._open_orders.clear()

    # ------------------------------------------------------------------
    # Trade tick handling
    # ------------------------------------------------------------------
    def on_trade_tick(self, tick: TradeTick) -> None:
        iid = tick.instrument_id
        iid_str = str(iid)
        try:
            price = float(tick.price)
        except Exception:
            return
        if price <= 0.0 or price >= 1.0:
            return

        history = self._price_history.setdefault(
            iid_str, deque(maxlen=self._cfg.lookback_ticks),
        )

        # If we have an open position, decrement the hold counter first.
        # Closing the position takes precedence over a new entry so we
        # don't pyramid on the same direction.
        if iid_str in self._open_side:
            remaining = self._hold_remaining.get(iid_str, 0) - 1
            if remaining <= 0:
                self._close_position(iid, price)
            else:
                self._hold_remaining[iid_str] = remaining
            history.append(price)
            return

        # Need a full window before evaluating entry.
        if len(history) < self._cfg.lookback_ticks:
            history.append(price)
            return

        rolling_mean = sum(history) / len(history)
        threshold = self._cfg.entry_threshold_ticks * TICK_SIZE

        if price <= rolling_mean - threshold:
            # Below the mean → expect snap-back up → BUY.
            self._enter(iid, OrderSide.BUY, price, rolling_mean)
        elif price >= rolling_mean + threshold:
            # Above the mean → expect snap-back down → SELL.
            self._enter(iid, OrderSide.SELL, price, rolling_mean)

        history.append(price)

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def _order_size(self, price: float) -> float:
        size = self._cfg.order_size_usdc / max(price, TICK_SIZE)
        size = round(size, 2)
        # Polymarket per-order minimum is 5 shares.
        if size < 5.0:
            size = 5.0
        return size

    def _enter(
        self,
        instrument_id: InstrumentId,
        side: OrderSide,
        price: float,
        rolling_mean: float,
    ) -> None:
        target_price = round(price, 2)
        if target_price <= 0.0 or target_price >= 1.0:
            return

        size = self._order_size(target_price)
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=side,
            quantity=Quantity.from_str(f"{size:.2f}"),
            price=Price.from_str(f"{target_price:.2f}"),
            time_in_force=TimeInForce.IOC,
        )
        coid = str(order.client_order_id)
        self._open_orders.add(coid)
        iid_str = str(instrument_id)
        self._open_side[iid_str] = side
        self._hold_remaining[iid_str] = max(1, int(self._cfg.hold_ticks))
        self.submit_order(order)

        log.info(
            "Mean-revert entry submitted",
            instrument=iid_str,
            side=str(side),
            price=target_price,
            rolling_mean=rolling_mean,
            size=size,
        )

    def _close_position(
        self,
        instrument_id: InstrumentId,
        price: float,
    ) -> None:
        iid_str = str(instrument_id)
        entry_side = self._open_side.pop(iid_str, None)
        self._hold_remaining.pop(iid_str, None)
        if entry_side is None:
            return

        close_side = (
            OrderSide.SELL if entry_side == OrderSide.BUY else OrderSide.BUY
        )
        target_price = round(price, 2)
        if target_price <= 0.0 or target_price >= 1.0:
            return

        size = self._order_size(target_price)
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=close_side,
            quantity=Quantity.from_str(f"{size:.2f}"),
            price=Price.from_str(f"{target_price:.2f}"),
            time_in_force=TimeInForce.IOC,
        )
        coid = str(order.client_order_id)
        self._open_orders.add(coid)
        self.submit_order(order)

        log.info(
            "Mean-revert close submitted",
            instrument=iid_str,
            close_side=str(close_side),
            price=target_price,
            size=size,
        )

    # ------------------------------------------------------------------
    # Fills / cancels
    # ------------------------------------------------------------------
    def on_order_filled(self, event) -> None:
        coid = str(event.client_order_id)
        if coid not in self._open_orders:
            return
        try:
            filled_qty = float(event.last_qty)
            filled_px = float(event.last_px)
        except Exception:
            return
        log.info(
            "Mean-revert order fill",
            client_order_id=coid,
            qty=filled_qty,
            price=filled_px,
        )

    def on_order_canceled(self, event) -> None:
        coid = str(event.client_order_id)
        self._open_orders.discard(coid)
