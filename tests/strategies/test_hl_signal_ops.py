"""Unit tests for shared Hyperliquid TradingView-clone signal helpers."""

from __future__ import annotations

import math

from trading_lab.strategies.hl_signal_ops import (
    atr_pct,
    classify_cloud,
    crossover,
    crossunder,
    exponential_moving_average,
    is_rising,
    rolling_high,
    rolling_low,
    simple_moving_average,
    supertrend_step,
    true_range,
    wilder_moving_average,
)


def test_crossover_detects_upward_cross() -> None:
    assert crossover(prev_left=99.0, prev_right=100.0, curr_left=101.0, curr_right=100.0)


def test_crossover_requires_prior_non_positive_spread() -> None:
    assert not crossover(prev_left=101.0, prev_right=100.0, curr_left=102.0, curr_right=100.0)


def test_crossunder_detects_downward_cross() -> None:
    assert crossunder(prev_left=101.0, prev_right=100.0, curr_left=99.0, curr_right=100.0)


def test_crossunder_requires_prior_non_negative_spread() -> None:
    assert not crossunder(prev_left=99.0, prev_right=100.0, curr_left=98.0, curr_right=100.0)


def test_is_rising_requires_strict_increase() -> None:
    assert is_rising([1.0, 2.0, 3.0])
    assert not is_rising([1.0, 2.0, 2.0])


def test_rolling_high_uses_last_n_values() -> None:
    assert rolling_high([1.0, 3.0, 2.0, 5.0], length=3) == 5.0


def test_rolling_low_uses_last_n_values() -> None:
    assert rolling_low([4.0, 3.0, 2.0, 5.0], length=3) == 2.0


def test_simple_moving_average_uses_trailing_window() -> None:
    assert math.isclose(simple_moving_average([1.0, 2.0, 3.0, 9.0], length=3), 14.0 / 3.0)


def test_exponential_moving_average_matches_seeded_recursive_formula() -> None:
    value = exponential_moving_average([1.0, 2.0, 3.0, 4.0, 5.0], length=3)
    assert math.isclose(value, 4.0)


def test_true_range_captures_gap_from_previous_close() -> None:
    assert true_range(high=110.0, low=105.0, prev_close=100.0) == 10.0


def test_atr_pct_scales_average_true_range_by_price() -> None:
    value = atr_pct([10.0, 20.0, 30.0], price=200.0)
    assert math.isclose(value, 10.0)


def test_wilder_moving_average_uses_recursive_smoothing() -> None:
    value = wilder_moving_average([10.0, 20.0, 30.0, 40.0], length=3)
    assert math.isclose(value, 80.0 / 3.0)


def test_supertrend_step_keeps_bullish_direction_above_lower_band() -> None:
    state = supertrend_step(
        high=110.0,
        low=100.0,
        close=109.0,
        prev_close=108.0,
        atr=2.0,
        multiplier=3.0,
        prev_final_upper=112.0,
        prev_final_lower=98.0,
        prev_direction=1,
    )
    assert math.isclose(state.final_upper, 111.0)
    assert math.isclose(state.final_lower, 99.0)
    assert state.direction == 1
    assert math.isclose(state.active_line, 99.0)


def test_supertrend_step_flips_bearish_when_close_breaks_lower_band() -> None:
    state = supertrend_step(
        high=108.0,
        low=96.0,
        close=97.0,
        prev_close=104.0,
        atr=2.0,
        multiplier=2.0,
        prev_final_upper=111.0,
        prev_final_lower=100.0,
        prev_direction=1,
    )
    assert state.direction == -1
    assert math.isclose(state.active_line, state.final_upper)


def test_classify_cloud_distinguishes_inside_above_and_below() -> None:
    assert classify_cloud(price=105.0, line_a=100.0, line_b=110.0) == "inside"
    assert classify_cloud(price=111.0, line_a=100.0, line_b=110.0) == "above"
    assert classify_cloud(price=99.0, line_a=100.0, line_b=110.0) == "below"
