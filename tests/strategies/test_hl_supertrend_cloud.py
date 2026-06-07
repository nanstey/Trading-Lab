"""Unit tests for Hyperliquid SuperTrend Cloud clone logic."""

from __future__ import annotations

import math

from trading_lab.strategies.hl_signal_ops import SuperTrendState
from trading_lab.strategies.hl_supertrend_cloud import (
    compute_cloud_snapshot,
    compute_supertrend_state,
    decide_cloud_action,
)


def test_compute_supertrend_state_requires_warmup_history() -> None:
    state = compute_supertrend_state(
        highs=[100.0, 101.0],
        lows=[95.0, 96.0],
        closes=[98.0, 99.0],
        atr_length=3,
        multiplier=3.0,
        prev_state=None,
    )
    assert state is None


def test_compute_supertrend_state_returns_bullish_state_after_warmup() -> None:
    state = compute_supertrend_state(
        highs=[100.0, 102.0, 104.0, 106.0],
        lows=[95.0, 97.0, 99.0, 101.0],
        closes=[98.0, 100.0, 103.0, 105.0],
        atr_length=3,
        multiplier=2.0,
        prev_state=SuperTrendState(final_upper=111.0, final_lower=94.0, direction=1),
    )
    assert state is not None
    assert state.direction == 1
    assert math.isclose(state.active_line, state.final_lower)


def test_compute_cloud_snapshot_classifies_price_above_cloud() -> None:
    snapshot = compute_cloud_snapshot(
        close=110.0,
        fast_state=SuperTrendState(final_upper=120.0, final_lower=101.0, direction=1),
        slow_state=SuperTrendState(final_upper=125.0, final_lower=103.0, direction=1),
    )
    assert snapshot.region == "above"


def test_decide_cloud_action_enters_long_on_cross_above() -> None:
    action = decide_cloud_action(
        prev_region="inside",
        curr_region="above",
        position_side="FLAT",
        allow_short=True,
        flatten_on_inside_cloud=True,
    )
    assert action == "ENTER_LONG"


def test_decide_cloud_action_enters_short_on_cross_below() -> None:
    action = decide_cloud_action(
        prev_region="inside",
        curr_region="below",
        position_side="FLAT",
        allow_short=True,
        flatten_on_inside_cloud=True,
    )
    assert action == "ENTER_SHORT"


def test_decide_cloud_action_exits_long_when_price_reenters_cloud() -> None:
    action = decide_cloud_action(
        prev_region="above",
        curr_region="inside",
        position_side="LONG",
        allow_short=True,
        flatten_on_inside_cloud=True,
    )
    assert action == "EXIT"


def test_decide_cloud_action_flips_short_when_long_breaks_below_cloud() -> None:
    action = decide_cloud_action(
        prev_region="inside",
        curr_region="below",
        position_side="LONG",
        allow_short=True,
        flatten_on_inside_cloud=True,
    )
    assert action == "FLIP_SHORT"


def test_decide_cloud_action_respects_long_only_mode() -> None:
    action = decide_cloud_action(
        prev_region="inside",
        curr_region="below",
        position_side="FLAT",
        allow_short=False,
        flatten_on_inside_cloud=True,
    )
    assert action == "HOLD"
