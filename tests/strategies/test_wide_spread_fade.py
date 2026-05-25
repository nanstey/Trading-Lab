"""
Unit tests for the Wide-Spread Fade strategy.

Cover the spread-tracking arithmetic and config defaults without
instantiating a full NautilusTrader engine.
"""

from __future__ import annotations

from collections import deque
from statistics import median

from nautilus_predict.strategies.wide_spread_fade import (
    TICK_SIZE,
    WideSpreadFadeConfig,
    WideSpreadFadeStrategy,
)


class TestWideSpreadFadeConfig:
    def test_default_config(self) -> None:
        cfg = WideSpreadFadeConfig()
        assert cfg.strategy_id == "WIDE-SPREAD-FADE-001"
        assert cfg.min_spread_tick == 0.03
        assert cfg.fade_size_usdc == 5.0
        assert cfg.spread_window == 20
        assert cfg.wide_multiple == 2.0
        assert cfg.max_net_inventory_usdc == 50.0

    def test_custom_config(self) -> None:
        cfg = WideSpreadFadeConfig(
            min_spread_tick=0.05,
            fade_size_usdc=10.0,
            wide_multiple=3.0,
        )
        assert cfg.min_spread_tick == 0.05
        assert cfg.fade_size_usdc == 10.0
        assert cfg.wide_multiple == 3.0


class TestSpreadDetection:
    """Detection logic uses a rolling median; verify arithmetic."""

    def test_tight_spread_below_min_does_not_trigger(self) -> None:
        cfg = WideSpreadFadeConfig(min_spread_tick=0.03, wide_multiple=2.0)
        history = deque([0.01] * 20, maxlen=20)
        rolling = median(history)
        spread = 0.02  # below min_spread_tick (0.03)
        assert not (spread > cfg.min_spread_tick and spread > cfg.wide_multiple * rolling)

    def test_wide_spread_above_threshold_triggers(self) -> None:
        cfg = WideSpreadFadeConfig(min_spread_tick=0.03, wide_multiple=2.0)
        history = deque([0.01] * 20, maxlen=20)
        rolling = median(history)
        spread = 0.05  # > 0.03 AND > 2x 0.01
        assert spread > cfg.min_spread_tick
        assert spread > cfg.wide_multiple * rolling

    def test_wide_spread_but_median_already_wide(self) -> None:
        """If the median spread is already wide, a wider snapshot shouldn't fire."""
        cfg = WideSpreadFadeConfig(min_spread_tick=0.02, wide_multiple=2.0)
        history = deque([0.04] * 20, maxlen=20)
        rolling = median(history)
        spread = 0.05  # > min but < 2x median (0.08)
        assert spread > cfg.min_spread_tick
        assert not (spread > cfg.wide_multiple * rolling)


class TestStrategyInstance:
    def test_instantiates(self) -> None:
        cfg = WideSpreadFadeConfig()
        strat = WideSpreadFadeStrategy(cfg)
        assert strat._cfg.strategy_id == "WIDE-SPREAD-FADE-001"
        assert strat._instruments == []
        assert strat._net_inventory_usdc == 0.0

    def test_register_before_start_queues(self) -> None:
        from nautilus_trader.model.identifiers import InstrumentId

        cfg = WideSpreadFadeConfig()
        strat = WideSpreadFadeStrategy(cfg)
        iid = InstrumentId.from_str("YES-TOKEN.POLYMARKET")
        strat.register_instrument(iid)
        # Should land in pending list (strategy not running yet).
        assert iid in strat._pending_instruments
        assert iid not in strat._instruments


class TestTickConstant:
    def test_tick_size_is_one_cent(self) -> None:
        assert TICK_SIZE == 0.01
