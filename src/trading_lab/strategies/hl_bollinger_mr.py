"""
Bollinger Z-score mean reversion for HL perps.

When close drops more than `entry_z` standard deviations below the rolling
SMA, go long (mean-revert up). When it rises `entry_z` σ above, go short.
Exit when close crosses back through the SMA.

Standard crypto-perp mean-revert baseline. Real edge depends heavily on
choice of lookback (short bars = noisy, long bars = stale) and z-threshold;
that's why we let walk-forward + DSR pick the params and tell us how confident
we should be.
"""

from __future__ import annotations

from collections import deque
from statistics import mean, pstdev

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class BollingerMRConfig(StrategyConfig, frozen=True):
    strategy_id: str = "HL-BOLLINGER-MR-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None

    lookback: int = 48          # bars in the rolling stat window
    entry_z: float = 2.0        # σ from mean to trigger entry
    exit_z: float = 0.25        # σ inside the mean to exit (close to 0)
    notional_usdc: float = 1000.0
    long_only: bool = False
    max_hold_bars: int = 72     # absolute hold-time cap; exit if still open


class BollingerMRStrategy(Strategy):
    def __init__(self, config: BollingerMRConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._closes: deque[float] = deque(maxlen=config.lookback + 1)
        self._side: str = "FLAT"
        self._held_bars: int = 0

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("BollingerMRStrategy requires bar_type + instrument_id")
        self.subscribe_bars(self._cfg.bar_type)

    def on_stop(self) -> None:
        try:
            self.close_all_positions(self._cfg.instrument_id)
        except Exception:
            pass

    def on_bar(self, bar: Bar) -> None:
        close = float(bar.close)
        self._closes.append(close)
        if len(self._closes) < self._cfg.lookback:
            return

        window = list(self._closes)[-self._cfg.lookback :]
        mu = mean(window)
        sigma = pstdev(window)
        if sigma == 0:
            return

        z = (close - mu) / sigma
        instrument = self.cache.instrument(self._cfg.instrument_id)
        if instrument is None:
            return
        size = Quantity(max(self._cfg.notional_usdc / max(close, 1e-9), 0), instrument.size_precision)
        if float(size) <= 0:
            return

        if self._side == "FLAT":
            if z <= -self._cfg.entry_z:
                self._send(OrderSide.BUY, size)
                self._side = "LONG"
                self._held_bars = 0
            elif (not self._cfg.long_only) and z >= self._cfg.entry_z:
                self._send(OrderSide.SELL, size)
                self._side = "SHORT"
                self._held_bars = 0
        else:
            self._held_bars += 1
            if self._side == "LONG":
                hit_exit = z >= -self._cfg.exit_z
                stale = self._held_bars >= self._cfg.max_hold_bars
                if hit_exit or stale:
                    self._send(OrderSide.SELL, size)
                    self._side = "FLAT"
            elif self._side == "SHORT":
                hit_exit = z <= self._cfg.exit_z
                stale = self._held_bars >= self._cfg.max_hold_bars
                if hit_exit or stale:
                    self._send(OrderSide.BUY, size)
                    self._side = "FLAT"

    def _send(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)


__all__ = ["BollingerMRConfig", "BollingerMRStrategy"]
