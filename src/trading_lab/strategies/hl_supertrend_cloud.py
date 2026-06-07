"""Hyperliquid SuperTrend Cloud clone for AlphaInsider / TradingView research."""

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
    SuperTrendState,
    classify_cloud,
    supertrend_step,
    true_range,
    wilder_moving_average,
)


class HLSuperTrendCloudConfig(StrategyConfig, frozen=True):
    strategy_id: str = "HL-SUPERTREND-CLOUD-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None

    fast_multiplier: float = 3.0
    fast_atr_length: int = 10
    slow_multiplier: float = 6.0
    slow_atr_length: int = 10
    notional_usdc: float = 1_000.0
    allow_short: bool = True
    flatten_on_inside_cloud: bool = True


@dataclass(frozen=True)
class CloudSnapshot:
    region: str
    fast_state: SuperTrendState
    slow_state: SuperTrendState


def compute_supertrend_state(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    atr_length: int,
    multiplier: float,
    prev_state: SuperTrendState | None,
) -> SuperTrendState | None:
    """Compute the next SuperTrend state from completed-bar history."""
    if atr_length <= 0 or len(closes) < max(atr_length, 2):
        return None

    true_ranges: list[float] = []
    start = len(closes) - atr_length
    for idx in range(start, len(closes)):
        prev_close = closes[idx - 1] if idx > 0 else None
        true_ranges.append(
            true_range(
                high=highs[idx],
                low=lows[idx],
                prev_close=prev_close,
            )
        )
    atr = wilder_moving_average(true_ranges, length=atr_length)
    return supertrend_step(
        high=highs[-1],
        low=lows[-1],
        close=closes[-1],
        prev_close=closes[-2],
        atr=atr,
        multiplier=multiplier,
        prev_final_upper=None if prev_state is None else prev_state.final_upper,
        prev_final_lower=None if prev_state is None else prev_state.final_lower,
        prev_direction=1 if prev_state is None else prev_state.direction,
    )


def compute_cloud_snapshot(
    *,
    close: float,
    fast_state: SuperTrendState,
    slow_state: SuperTrendState,
) -> CloudSnapshot:
    return CloudSnapshot(
        region=classify_cloud(
            price=close,
            line_a=fast_state.active_line,
            line_b=slow_state.active_line,
        ),
        fast_state=fast_state,
        slow_state=slow_state,
    )


def decide_cloud_action(
    *,
    prev_region: str | None,
    curr_region: str,
    position_side: str,
    allow_short: bool,
    flatten_on_inside_cloud: bool,
) -> str:
    """
    Clone-level decision policy inferred from public summaries.

    We treat entries as the close crossing from not-above to above the cloud
    (long) or from not-below to below the cloud (short). When already in a
    position, a return inside the cloud flattens; a break through the opposite
    side flips if shorts are enabled.
    """
    if prev_region is None:
        return "HOLD"

    crossed_above = prev_region != "above" and curr_region == "above"
    crossed_below = prev_region != "below" and curr_region == "below"
    back_inside = curr_region == "inside"

    if position_side == "FLAT":
        if crossed_above:
            return "ENTER_LONG"
        if allow_short and crossed_below:
            return "ENTER_SHORT"
        return "HOLD"

    if position_side == "LONG":
        if allow_short and crossed_below:
            return "FLIP_SHORT"
        if flatten_on_inside_cloud and back_inside:
            return "EXIT"
        return "HOLD"

    if position_side == "SHORT":
        if crossed_above:
            return "FLIP_LONG"
        if flatten_on_inside_cloud and back_inside:
            return "EXIT"
        return "HOLD"

    raise ValueError(f"unknown position_side={position_side}")


class HLSuperTrendCloudStrategy(Strategy):
    """Bar-close SuperTrend cloud strategy for a single HL perp instrument."""

    def __init__(self, config: HLSuperTrendCloudConfig) -> None:
        super().__init__(config)
        self._cfg = config
        window = max(config.fast_atr_length, config.slow_atr_length) + 2
        self._highs: deque[float] = deque(maxlen=window)
        self._lows: deque[float] = deque(maxlen=window)
        self._closes: deque[float] = deque(maxlen=window)
        self._fast_state: SuperTrendState | None = None
        self._slow_state: SuperTrendState | None = None
        self._prev_region: str | None = None
        self._position_side: str = "FLAT"

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("HLSuperTrendCloudStrategy requires bar_type + instrument_id")
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

        fast_state = compute_supertrend_state(
            highs=list(self._highs),
            lows=list(self._lows),
            closes=list(self._closes),
            atr_length=self._cfg.fast_atr_length,
            multiplier=self._cfg.fast_multiplier,
            prev_state=self._fast_state,
        )
        slow_state = compute_supertrend_state(
            highs=list(self._highs),
            lows=list(self._lows),
            closes=list(self._closes),
            atr_length=self._cfg.slow_atr_length,
            multiplier=self._cfg.slow_multiplier,
            prev_state=self._slow_state,
        )
        if fast_state is None or slow_state is None:
            return

        snapshot = compute_cloud_snapshot(close=close, fast_state=fast_state, slow_state=slow_state)
        action = decide_cloud_action(
            prev_region=self._prev_region,
            curr_region=snapshot.region,
            position_side=self._position_side,
            allow_short=self._cfg.allow_short,
            flatten_on_inside_cloud=self._cfg.flatten_on_inside_cloud,
        )

        instrument = self.cache.instrument(self._cfg.instrument_id)
        if instrument is None:
            self._fast_state = fast_state
            self._slow_state = slow_state
            self._prev_region = snapshot.region
            return

        qty_units = max(self._cfg.notional_usdc / max(close, 1e-9), 0.0)
        size = Quantity(qty_units, instrument.size_precision)
        if float(size) > 0:
            if action == "ENTER_LONG":
                self._send(OrderSide.BUY, size)
                self._position_side = "LONG"
            elif action == "ENTER_SHORT":
                self._send(OrderSide.SELL, size)
                self._position_side = "SHORT"
            elif action == "EXIT":
                exit_side = OrderSide.SELL if self._position_side == "LONG" else OrderSide.BUY
                self._send(exit_side, size)
                self._position_side = "FLAT"
            elif action == "FLIP_LONG":
                self._send(OrderSide.BUY, Quantity(2 * float(size), instrument.size_precision))
                self._position_side = "LONG"
            elif action == "FLIP_SHORT":
                self._send(OrderSide.SELL, Quantity(2 * float(size), instrument.size_precision))
                self._position_side = "SHORT"

        self._fast_state = fast_state
        self._slow_state = slow_state
        self._prev_region = snapshot.region

    def _send(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)


__all__ = [
    "CloudSnapshot",
    "compute_cloud_snapshot",
    "compute_supertrend_state",
    "decide_cloud_action",
    "HLSuperTrendCloudConfig",
    "HLSuperTrendCloudStrategy",
]
