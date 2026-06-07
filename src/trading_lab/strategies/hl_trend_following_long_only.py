"""Hyperliquid Trend Following Long Only clone for AlphaInsider / TradingView research.

This is a conservative first-pass implementation from secondary-source evidence.
The recovered Pine excerpt preserved the four visible inputs, the smoothed
high/low channel construction, and the long-entry / exit triggers, but it did
not preserve every line of the original script. We therefore keep the remaining
policy explicit:
- BTC-only on 1d bars for the first pass
- smoothed channel from EMA(lowest(low, lookback)) / EMA(highest(high, lookback))
- ATR hysteresis bands around that smoothed channel
- long entry only when the inferred trend state crosses from <= 0 to > 0
- long exit only when the inferred trend state crosses from >= 0 to < 0
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from trading_lab.strategies.hl_signal_ops import (
    crossover,
    crossunder,
    exponential_moving_average,
    rolling_high,
    rolling_low,
    true_range,
)


class HLTrendFollowingLongOnlyConfig(StrategyConfig, frozen=True):
    strategy_id: str = "HL-TREND-FOLLOWING-LONG-ONLY-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None

    lookback_length: int = 200
    smoother_length: int = 3
    atr_length: int = 10
    atr_multiplier: float = 0.5
    notional_usdc: float = 1_000.0
    long_only: bool = True


@dataclass(frozen=True)
class TrendFollowingSnapshot:
    smoothed_high: float
    smoothed_low: float
    atr: float
    upper_band: float
    lower_band: float
    prev_trend: float
    trend: float


def _smoothed_channel(values: list[float], *, lookback_length: int, smoother_length: int, use_high: bool) -> float:
    channel_values: list[float] = []
    for end_idx in range(lookback_length, len(values) + 1):
        window = values[:end_idx]
        if use_high:
            channel_values.append(rolling_high(window, length=lookback_length))
        else:
            channel_values.append(rolling_low(window, length=lookback_length))
    return exponential_moving_average(channel_values, length=smoother_length)


def _average_true_range(highs: list[float], lows: list[float], closes: list[float], *, atr_length: int) -> float:
    true_ranges: list[float] = []
    start = len(closes) - atr_length
    for idx in range(start, len(closes)):
        true_ranges.append(
            true_range(
                high=highs[idx],
                low=lows[idx],
                prev_close=closes[idx - 1] if idx > 0 else None,
            )
        )
    return sum(true_ranges) / len(true_ranges)


def _trend_state(*, close: float, upper_band: float, lower_band: float, prev_state: float) -> float:
    if close > upper_band:
        return 1.0
    if close < lower_band:
        return -1.0
    return prev_state


def compute_trend_following_snapshot(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    lookback_length: int,
    smoother_length: int,
    atr_length: int,
    atr_multiplier: float,
) -> TrendFollowingSnapshot | None:
    """Compute the recovered trend-regime snapshot from completed bars."""
    warmup = max(lookback_length + smoother_length - 1, atr_length + 1) + 1
    if len(highs) < warmup or len(lows) < warmup or len(closes) < warmup:
        return None

    prev_highs = highs[:-1]
    prev_lows = lows[:-1]
    prev_closes = closes[:-1]

    prev_smoothed_high = _smoothed_channel(
        prev_highs,
        lookback_length=lookback_length,
        smoother_length=smoother_length,
        use_high=True,
    )
    prev_smoothed_low = _smoothed_channel(
        prev_lows,
        lookback_length=lookback_length,
        smoother_length=smoother_length,
        use_high=False,
    )
    prev_atr = _average_true_range(prev_highs, prev_lows, prev_closes, atr_length=atr_length)
    prev_upper_band = prev_smoothed_high - (atr_multiplier * prev_atr)
    prev_lower_band = prev_smoothed_low + (atr_multiplier * prev_atr)

    baseline_prev_close = prev_closes[-2]
    prev_trend = _trend_state(
        close=prev_closes[-1],
        upper_band=prev_upper_band,
        lower_band=prev_lower_band,
        prev_state=1.0 if baseline_prev_close > prev_upper_band else -1.0,
    )

    smoothed_high = _smoothed_channel(
        highs,
        lookback_length=lookback_length,
        smoother_length=smoother_length,
        use_high=True,
    )
    smoothed_low = _smoothed_channel(
        lows,
        lookback_length=lookback_length,
        smoother_length=smoother_length,
        use_high=False,
    )
    atr = _average_true_range(highs, lows, closes, atr_length=atr_length)
    upper_band = smoothed_high - (atr_multiplier * atr)
    lower_band = smoothed_low + (atr_multiplier * atr)
    trend = _trend_state(
        close=closes[-1],
        upper_band=upper_band,
        lower_band=lower_band,
        prev_state=prev_trend,
    )

    return TrendFollowingSnapshot(
        smoothed_high=smoothed_high,
        smoothed_low=smoothed_low,
        atr=atr,
        upper_band=upper_band,
        lower_band=lower_band,
        prev_trend=prev_trend,
        trend=trend,
    )


def decide_trend_following_action(*, snapshot: TrendFollowingSnapshot, position_side: str) -> str:
    """Long-only action policy from the recovered trend-state crossover rules."""
    crossed_up = crossover(
        prev_left=snapshot.prev_trend,
        prev_right=0.0,
        curr_left=snapshot.trend,
        curr_right=0.0,
    )
    crossed_down = crossunder(
        prev_left=snapshot.prev_trend,
        prev_right=0.0,
        curr_left=snapshot.trend,
        curr_right=0.0,
    )

    if position_side == "FLAT":
        return "ENTER_LONG" if crossed_up else "HOLD"

    if position_side == "LONG":
        return "EXIT" if crossed_down else "HOLD"

    raise ValueError(f"unknown position_side={position_side}")


class HLTrendFollowingLongOnlyStrategy(Strategy):
    """BTC-first long-only channel / ATR trend strategy for a single HL perp."""

    def __init__(self, config: HLTrendFollowingLongOnlyConfig) -> None:
        super().__init__(config)
        self._cfg = config
        window = max(config.lookback_length + config.smoother_length + 1, config.atr_length + 2)
        self._highs: deque[float] = deque(maxlen=window)
        self._lows: deque[float] = deque(maxlen=window)
        self._closes: deque[float] = deque(maxlen=window)
        self._position_side: str = "FLAT"

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("HLTrendFollowingLongOnlyStrategy requires bar_type + instrument_id")
        self.subscribe_bars(self._cfg.bar_type)

    def on_stop(self) -> None:
        try:
            self.close_all_positions(self._cfg.instrument_id)
        except Exception:
            pass

    def on_bar(self, bar: Bar) -> None:
        close = float(bar.close)
        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))
        self._closes.append(close)

        snapshot = compute_trend_following_snapshot(
            highs=list(self._highs),
            lows=list(self._lows),
            closes=list(self._closes),
            lookback_length=self._cfg.lookback_length,
            smoother_length=self._cfg.smoother_length,
            atr_length=self._cfg.atr_length,
            atr_multiplier=self._cfg.atr_multiplier,
        )
        if snapshot is None:
            return

        action = decide_trend_following_action(snapshot=snapshot, position_side=self._position_side)
        instrument = self.cache.instrument(self._cfg.instrument_id)
        if instrument is None:
            return

        qty_units = max(self._cfg.notional_usdc / max(close, 1e-9), 0.0)
        size = Quantity(qty_units, instrument.size_precision)
        if float(size) <= 0:
            return

        if action == "ENTER_LONG":
            self._send(OrderSide.BUY, size)
            self._position_side = "LONG"
        elif action == "EXIT":
            self._send(OrderSide.SELL, size)
            self._position_side = "FLAT"

    def _send(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)


__all__ = [
    "HLTrendFollowingLongOnlyConfig",
    "HLTrendFollowingLongOnlyStrategy",
    "TrendFollowingSnapshot",
    "compute_trend_following_snapshot",
    "decide_trend_following_action",
]
