"""Unit tests for Hyperliquid Ichimoku 3h clone logic."""

from __future__ import annotations

import math

from trading_lab.strategies.hl_ichimoku_3h import (
    IchimokuSnapshot,
    compute_ichimoku_snapshot,
    decide_ichimoku_action,
)


def test_compute_ichimoku_snapshot_requires_warmup_history() -> None:
    snapshot = compute_ichimoku_snapshot(
        highs=[101.0] * 10,
        lows=[99.0] * 10,
        closes=[100.0] * 10,
        tenkan_length=22,
        kijun_length=60,
        senkou_b_length=120,
        displacement=30,
        rsi_length=14,
        rsi_threshold=50.0,
        volatility_length=14,
        volatility_gate_threshold=0.2,
    )
    assert snapshot is None


def test_compute_ichimoku_snapshot_returns_bullish_state() -> None:
    highs = [100.0 + i for i in range(140)]
    lows = [value - 4.0 for value in highs]
    closes = [value - 1.0 for value in highs]

    snapshot = compute_ichimoku_snapshot(
        highs=highs,
        lows=lows,
        closes=closes,
        tenkan_length=22,
        kijun_length=60,
        senkou_b_length=120,
        displacement=30,
        rsi_length=14,
        rsi_threshold=50.0,
        volatility_length=14,
        volatility_gate_threshold=0.2,
    )

    assert snapshot is not None
    assert snapshot.above_cloud is True
    assert snapshot.chikou_bullish is True
    assert snapshot.long_entry_ready is True
    assert snapshot.rsi > 50.0
    assert math.isclose(snapshot.tenkan, (max(highs[-22:]) + min(lows[-22:])) / 2.0)


def test_decide_ichimoku_action_enters_long_when_filters_align() -> None:
    snapshot = IchimokuSnapshot(
        close=130.0,
        tenkan=125.0,
        kijun=120.0,
        span_a=118.0,
        span_b=116.0,
        chikou_reference_close=110.0,
        rsi=62.0,
        volatility_pct=0.45,
        rsi_threshold=50.0,
        volatility_gate_threshold=0.2,
    )
    assert decide_ichimoku_action(snapshot=snapshot, position_side="FLAT", allow_short=True) == "ENTER_LONG"


def test_decide_ichimoku_action_enters_short_when_filters_align() -> None:
    snapshot = IchimokuSnapshot(
        close=90.0,
        tenkan=95.0,
        kijun=100.0,
        span_a=101.0,
        span_b=103.0,
        chikou_reference_close=110.0,
        rsi=38.0,
        volatility_pct=0.45,
        rsi_threshold=50.0,
        volatility_gate_threshold=0.2,
    )
    assert decide_ichimoku_action(snapshot=snapshot, position_side="FLAT", allow_short=True) == "ENTER_SHORT"


def test_decide_ichimoku_action_exits_long_when_cloud_breaks() -> None:
    snapshot = IchimokuSnapshot(
        close=118.0,
        tenkan=122.0,
        kijun=120.0,
        span_a=121.0,
        span_b=119.0,
        chikou_reference_close=110.0,
        rsi=48.0,
        volatility_pct=0.45,
        rsi_threshold=50.0,
        volatility_gate_threshold=0.2,
    )
    assert decide_ichimoku_action(snapshot=snapshot, position_side="LONG", allow_short=True) == "EXIT"


def test_decide_ichimoku_action_respects_long_only_mode() -> None:
    snapshot = IchimokuSnapshot(
        close=90.0,
        tenkan=95.0,
        kijun=100.0,
        span_a=101.0,
        span_b=103.0,
        chikou_reference_close=110.0,
        rsi=38.0,
        volatility_pct=0.45,
        rsi_threshold=50.0,
        volatility_gate_threshold=0.2,
    )
    assert decide_ichimoku_action(snapshot=snapshot, position_side="FLAT", allow_short=False) == "HOLD"
