"""Unit tests for Hyperliquid Trend Following Long Only clone logic."""

from __future__ import annotations

from trading_lab.strategies.hl_trend_following_long_only import (
    TrendFollowingSnapshot,
    compute_trend_following_snapshot,
    decide_trend_following_action,
)


def test_compute_trend_following_snapshot_requires_warmup_history() -> None:
    snapshot = compute_trend_following_snapshot(
        highs=[100.0] * 7,
        lows=[90.0] * 7,
        closes=[95.0] * 7,
        lookback_length=5,
        smoother_length=3,
        atr_length=3,
        atr_multiplier=0.5,
    )
    assert snapshot is None


def test_compute_trend_following_snapshot_detects_bullish_crossover() -> None:
    highs = [
        *([100.0] * 200),
        100.0,
        100.0,
        100.0,
        104.0,
    ]
    lows = [
        *([95.0] * 200),
        95.0,
        95.0,
        95.0,
        99.0,
    ]
    closes = [
        *([97.0] * 200),
        96.0,
        96.0,
        96.0,
        104.5,
    ]

    snapshot = compute_trend_following_snapshot(
        highs=highs,
        lows=lows,
        closes=closes,
        lookback_length=200,
        smoother_length=3,
        atr_length=3,
        atr_multiplier=0.5,
    )

    assert snapshot is not None
    assert snapshot.prev_trend == -1.0
    assert snapshot.trend == 1.0
    assert snapshot.upper_band > snapshot.lower_band


def test_decide_trend_following_action_enters_long_on_crossover() -> None:
    action = decide_trend_following_action(
        snapshot=TrendFollowingSnapshot(
            smoothed_high=102.0,
            smoothed_low=96.0,
            atr=2.0,
            upper_band=101.0,
            lower_band=97.0,
            prev_trend=-1.0,
            trend=1.0,
        ),
        position_side="FLAT",
    )
    assert action == "ENTER_LONG"


def test_decide_trend_following_action_exits_on_crossunder() -> None:
    action = decide_trend_following_action(
        snapshot=TrendFollowingSnapshot(
            smoothed_high=102.0,
            smoothed_low=96.0,
            atr=2.0,
            upper_band=101.0,
            lower_band=97.0,
            prev_trend=1.0,
            trend=-1.0,
        ),
        position_side="LONG",
    )
    assert action == "EXIT"


def test_decide_trend_following_action_holds_without_new_signal() -> None:
    action = decide_trend_following_action(
        snapshot=TrendFollowingSnapshot(
            smoothed_high=102.0,
            smoothed_low=96.0,
            atr=2.0,
            upper_band=101.0,
            lower_band=97.0,
            prev_trend=1.0,
            trend=1.0,
        ),
        position_side="LONG",
    )
    assert action == "HOLD"
