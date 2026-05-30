"""
Donchian channel breakout for HL perps.

Classic trend-following baseline: enter long when the current bar's close
takes out the highest close of the prior `entry_lookback` bars, flip short
(or exit, if `long_only`) when it breaks the lowest close of the prior
`exit_lookback` bars. NETTING accounting — flipping closes the existing
position and opens the opposite in one market order.

Bar-driven (one decision per bar close). Position size is a fixed USDC
notional converted to base units at the close price; survives across the
backtest window without margin checks because the runner uses MARGIN
account with default 10× headroom.

Used as a smoke / baseline strategy for the HL backtest harness — not a
production edge claim. Performance varies by interval and lookback; that's
why we have walk-forward + DSR to keep us honest.
"""

from __future__ import annotations

from collections import deque

import structlog
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

log = structlog.get_logger(__name__)


class DonchianBreakoutConfig(StrategyConfig, frozen=True):
    """Configuration for the Donchian breakout strategy."""

    strategy_id: str = "HL-DONCHIAN-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None

    entry_lookback: int = 24       # bars
    exit_lookback: int = 12        # bars
    notional_usdc: float = 1000.0  # per entry
    long_only: bool = False
    cooldown_bars: int = 1         # bars to wait after exit before re-entering


class DonchianBreakoutStrategy(Strategy):
    """Bar-driven Donchian breakout for a single HL perp instrument."""

    def __init__(self, config: DonchianBreakoutConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._closes: deque[float] = deque(maxlen=max(config.entry_lookback, config.exit_lookback) + 1)
        self._highs: deque[float] = deque(maxlen=config.entry_lookback + 1)
        self._lows: deque[float] = deque(maxlen=config.exit_lookback + 1)
        self._position_side: str = "FLAT"  # FLAT | LONG | SHORT
        self._bars_since_exit: int = 9999

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("DonchianBreakoutStrategy requires bar_type + instrument_id")
        self.subscribe_bars(self._cfg.bar_type)

    def on_stop(self) -> None:
        try:
            self.close_all_positions(self._cfg.instrument_id)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Bar handler
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        self._closes.append(close)
        self._highs.append(high)
        self._lows.append(low)
        self._bars_since_exit += 1

        # Need enough history.
        if len(self._highs) < self._cfg.entry_lookback + 1:
            return
        if len(self._lows) < self._cfg.exit_lookback + 1:
            return

        # Channels exclude the current bar (use bars[-(N+1):-1]).
        upper = max(list(self._highs)[:-1][-self._cfg.entry_lookback :])
        lower = min(list(self._lows)[:-1][-self._cfg.exit_lookback :])

        instrument = self.cache.instrument(self._cfg.instrument_id)
        if instrument is None:
            return

        qty_units = max(self._cfg.notional_usdc / max(close, 1e-9), 0.0)
        # Round size to instrument precision via Quantity construction.
        size = Quantity(qty_units, instrument.size_precision)
        if float(size) <= 0:
            return

        if self._position_side == "FLAT" and self._bars_since_exit >= self._cfg.cooldown_bars:
            if close > upper:
                self._enter(OrderSide.BUY, size)
                self._position_side = "LONG"
            elif not self._cfg.long_only and close < lower:
                self._enter(OrderSide.SELL, size)
                self._position_side = "SHORT"

        elif self._position_side == "LONG":
            if close < lower:
                # Exit long; flip short if allowed.
                if not self._cfg.long_only:
                    self._enter(OrderSide.SELL, Quantity(2 * float(size), instrument.size_precision))
                    self._position_side = "SHORT"
                else:
                    self._enter(OrderSide.SELL, size)
                    self._position_side = "FLAT"
                    self._bars_since_exit = 0

        elif self._position_side == "SHORT":
            if close > upper:
                self._enter(OrderSide.BUY, Quantity(2 * float(size), instrument.size_precision))
                self._position_side = "LONG"

    # ------------------------------------------------------------------
    # Order helpers
    # ------------------------------------------------------------------

    def _enter(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)


__all__ = ["DonchianBreakoutConfig", "DonchianBreakoutStrategy"]
