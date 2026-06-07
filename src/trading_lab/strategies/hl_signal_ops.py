"""Shared signal helpers for Hyperliquid TradingView-style strategy ports."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence


def crossover(*, prev_left: float, prev_right: float, curr_left: float, curr_right: float) -> bool:
    """Return True when left crosses from at/below right to above right."""
    return prev_left <= prev_right and curr_left > curr_right



def crossunder(*, prev_left: float, prev_right: float, curr_left: float, curr_right: float) -> bool:
    """Return True when left crosses from at/above right to below right."""
    return prev_left >= prev_right and curr_left < curr_right



def is_rising(values: Sequence[float]) -> bool:
    """Return True when each successive value is strictly greater than the prior."""
    if len(values) < 2:
        return False
    return all(curr > prev for prev, curr in zip(values, values[1:], strict=False))



def rolling_high(values: Sequence[float], *, length: int) -> float:
    """Return the maximum over the trailing length window."""
    _validate_window(values, length)
    return max(values[-length:])



def rolling_low(values: Sequence[float], *, length: int) -> float:
    """Return the minimum over the trailing length window."""
    _validate_window(values, length)
    return min(values[-length:])



def simple_moving_average(values: Sequence[float], *, length: int) -> float:
    """Return the trailing simple moving average over the requested window."""
    _validate_window(values, length)
    window = values[-length:]
    return sum(window) / length



def exponential_moving_average(values: Sequence[float], *, length: int) -> float:
    """Return a TradingView-style EMA seeded from the first full SMA window."""
    _validate_window(values, length)
    alpha = 2.0 / (length + 1)
    ema = sum(values[:length]) / length
    for value in values[length:]:
        ema = (value * alpha) + (ema * (1.0 - alpha))
    return ema



def true_range(*, high: float, low: float, prev_close: float | None) -> float:
    """Classic Wilder true range, including gap moves from the prior close."""
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))



def atr_pct(true_ranges: Sequence[float], *, price: float) -> float:
    """Average true range as a percent of the current price."""
    if not true_ranges or price <= 0:
        return 0.0
    return (sum(true_ranges) / len(true_ranges)) / price * 100.0


@dataclass(frozen=True)
class SuperTrendState:
    """One-bar SuperTrend result."""

    final_upper: float
    final_lower: float
    direction: int  # 1=bullish (lower band active), -1=bearish (upper band active)

    @property
    def active_line(self) -> float:
        return self.final_lower if self.direction > 0 else self.final_upper


def wilder_moving_average(values: Sequence[float], *, length: int) -> float:
    """Return Wilder's recursive moving average seeded from the first SMA window."""
    _validate_window(values, length)
    rma = sum(values[:length]) / length
    for value in values[length:]:
        rma = ((rma * (length - 1)) + value) / length
    return rma


def supertrend_step(
    *,
    high: float,
    low: float,
    close: float,
    prev_close: float,
    atr: float,
    multiplier: float,
    prev_final_upper: float | None,
    prev_final_lower: float | None,
    prev_direction: int,
) -> SuperTrendState:
    """Compute the next SuperTrend band state for one completed bar."""
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    final_upper = basic_upper
    if prev_final_upper is not None and basic_upper >= prev_final_upper and prev_close <= prev_final_upper:
        final_upper = prev_final_upper

    final_lower = basic_lower
    if prev_final_lower is not None and basic_lower <= prev_final_lower and prev_close >= prev_final_lower:
        final_lower = prev_final_lower

    direction = prev_direction if prev_direction in (-1, 1) else 1
    if direction > 0 and close < final_lower:
        direction = -1
    elif direction < 0 and close > final_upper:
        direction = 1

    return SuperTrendState(
        final_upper=final_upper,
        final_lower=final_lower,
        direction=direction,
    )


def classify_cloud(*, price: float, line_a: float, line_b: float) -> str:
    """Classify a price as above, below, or inside the cloud between two lines."""
    upper = max(line_a, line_b)
    lower = min(line_a, line_b)
    if price > upper:
        return "above"
    if price < lower:
        return "below"
    return "inside"



def _validate_window(values: Sequence[float], length: int) -> None:
    if length <= 0:
        raise ValueError("length must be positive")
    if len(values) < length:
        raise ValueError("not enough values for requested window")


__all__ = [
    "atr_pct",
    "classify_cloud",
    "crossover",
    "crossunder",
    "exponential_moving_average",
    "is_rising",
    "rolling_high",
    "rolling_low",
    "simple_moving_average",
    "SuperTrendState",
    "supertrend_step",
    "true_range",
    "wilder_moving_average",
]
