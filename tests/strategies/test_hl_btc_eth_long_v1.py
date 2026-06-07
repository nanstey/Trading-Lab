"""Unit tests for Hyperliquid BTC/ETH Long v1 clone logic."""

from __future__ import annotations

from trading_lab.strategies.hl_btc_eth_long_v1 import (
    BTCEthLongSnapshot,
    compute_btc_eth_long_snapshot,
    decide_btc_eth_long_action,
)


def _trend_series(length: int = 220, *, start: float = 100.0, growth: float = 1.01) -> tuple[list[float], list[float], list[float]]:
    closes = [start * (growth**idx) for idx in range(length)]
    highs = [close * 1.004 for close in closes]
    lows = [close * 0.996 for close in closes]
    return highs, lows, closes


def test_compute_btc_eth_long_snapshot_requires_warmup() -> None:
    highs, lows, closes = _trend_series(length=40)
    snapshot = compute_btc_eth_long_snapshot(
        highs=highs,
        lows=lows,
        closes=closes,
        ema_length=20,
        sma_length=100,
        slow_sma_length=200,
        atr_length=14,
        macd_fast_length=12,
        macd_slow_length=26,
        macd_signal_length=7,
        volatility_cap_pct=2.0,
    )
    assert snapshot is None


def test_compute_btc_eth_long_snapshot_flags_entry_ready_for_clean_uptrend() -> None:
    highs, lows, closes = _trend_series()
    snapshot = compute_btc_eth_long_snapshot(
        highs=highs,
        lows=lows,
        closes=closes,
        ema_length=20,
        sma_length=100,
        slow_sma_length=200,
        atr_length=14,
        macd_fast_length=12,
        macd_slow_length=26,
        macd_signal_length=7,
        volatility_cap_pct=2.0,
    )
    assert snapshot is not None
    assert snapshot.price_filter_ok is True
    assert snapshot.trend_ready is True
    assert snapshot.volatility_ok is True
    assert snapshot.entry_ready is True


def test_compute_btc_eth_long_snapshot_rejects_high_volatility() -> None:
    highs, lows, closes = _trend_series()
    highs[-14:] = [close + 20.0 for close in closes[-14:]]
    lows[-14:] = [close - 20.0 for close in closes[-14:]]
    snapshot = compute_btc_eth_long_snapshot(
        highs=highs,
        lows=lows,
        closes=closes,
        ema_length=20,
        sma_length=100,
        slow_sma_length=200,
        atr_length=14,
        macd_fast_length=12,
        macd_slow_length=26,
        macd_signal_length=7,
        volatility_cap_pct=2.0,
    )
    assert snapshot is not None
    assert snapshot.volatility_pct > 2.0
    assert snapshot.volatility_ok is False
    assert snapshot.entry_ready is False


def test_decide_btc_eth_long_action_enters_long_when_filters_align() -> None:
    action = decide_btc_eth_long_action(
        snapshot=BTCEthLongSnapshot(
            close=250.0,
            ema=245.0,
            prev_ema=244.0,
            sma=240.0,
            prev_sma=239.0,
            slow_sma=220.0,
            prev_slow_sma=219.0,
            macd_line=5.0,
            prev_macd_line=4.5,
            signal_line=4.8,
            volatility_pct=1.2,
            volatility_cap_pct=2.0,
        ),
        position_side="FLAT",
        entry_price=None,
        stop_loss_pct=1.5,
    )
    assert action == "ENTER_LONG"


def test_decide_btc_eth_long_action_exits_on_stop_loss() -> None:
    action = decide_btc_eth_long_action(
        snapshot=BTCEthLongSnapshot(
            close=98.0,
            ema=101.0,
            prev_ema=100.5,
            sma=100.0,
            prev_sma=99.5,
            slow_sma=95.0,
            prev_slow_sma=94.5,
            macd_line=2.0,
            prev_macd_line=1.5,
            signal_line=1.8,
            volatility_pct=1.0,
            volatility_cap_pct=2.0,
        ),
        position_side="LONG",
        entry_price=100.0,
        stop_loss_pct=1.5,
    )
    assert action == "EXIT"


def test_decide_btc_eth_long_action_exits_on_ema_sma_crossunder() -> None:
    action = decide_btc_eth_long_action(
        snapshot=BTCEthLongSnapshot(
            close=150.0,
            ema=99.0,
            prev_ema=101.0,
            sma=100.0,
            prev_sma=100.0,
            slow_sma=90.0,
            prev_slow_sma=89.0,
            macd_line=0.5,
            prev_macd_line=0.7,
            signal_line=0.6,
            volatility_pct=1.0,
            volatility_cap_pct=2.0,
        ),
        position_side="LONG",
        entry_price=120.0,
        stop_loss_pct=1.5,
    )
    assert action == "EXIT"
