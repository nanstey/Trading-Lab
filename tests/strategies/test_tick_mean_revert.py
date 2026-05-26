"""
Unit tests for the Tick Mean-Reversion strategy.

Cover the rolling-mean arithmetic and config defaults without
instantiating a full NautilusTrader engine.
"""

from __future__ import annotations

from collections import deque

from trading_lab.strategies.tick_mean_revert import (
    TICK_SIZE,
    TickMeanRevertConfig,
    TickMeanRevertStrategy,
)


class TestTickMeanRevertConfig:
    def test_default_config(self) -> None:
        cfg = TickMeanRevertConfig()
        assert cfg.strategy_id == "TICK-MEAN-REVERT-001"
        assert cfg.lookback_ticks == 20
        assert cfg.entry_threshold_ticks == 1
        assert cfg.hold_ticks == 3
        assert cfg.order_size_usdc == 5.0

    def test_custom_config(self) -> None:
        cfg = TickMeanRevertConfig(
            lookback_ticks=30,
            entry_threshold_ticks=2,
            hold_ticks=5,
            order_size_usdc=10.0,
        )
        assert cfg.lookback_ticks == 30
        assert cfg.entry_threshold_ticks == 2
        assert cfg.hold_ticks == 5
        assert cfg.order_size_usdc == 10.0


class TestMeanReversionArithmetic:
    """Entry threshold uses rolling mean of recent prices."""

    def test_below_mean_triggers_buy_condition(self) -> None:
        cfg = TickMeanRevertConfig(lookback_ticks=10, entry_threshold_ticks=1)
        history = deque([0.50] * 10, maxlen=10)
        rolling_mean = sum(history) / len(history)
        price = 0.48  # 2 ticks below mean
        threshold = cfg.entry_threshold_ticks * TICK_SIZE
        assert price <= rolling_mean - threshold

    def test_above_mean_triggers_sell_condition(self) -> None:
        cfg = TickMeanRevertConfig(lookback_ticks=10, entry_threshold_ticks=1)
        history = deque([0.50] * 10, maxlen=10)
        rolling_mean = sum(history) / len(history)
        price = 0.52  # 2 ticks above mean
        threshold = cfg.entry_threshold_ticks * TICK_SIZE
        assert price >= rolling_mean + threshold

    def test_price_at_mean_does_not_trigger(self) -> None:
        cfg = TickMeanRevertConfig(lookback_ticks=10, entry_threshold_ticks=2)
        history = deque([0.50] * 10, maxlen=10)
        rolling_mean = sum(history) / len(history)
        price = 0.50
        threshold = cfg.entry_threshold_ticks * TICK_SIZE
        assert not (price <= rolling_mean - threshold)
        assert not (price >= rolling_mean + threshold)

    def test_threshold_two_ticks_filters_single_tick_noise(self) -> None:
        cfg = TickMeanRevertConfig(lookback_ticks=10, entry_threshold_ticks=2)
        history = deque([0.50] * 10, maxlen=10)
        rolling_mean = sum(history) / len(history)
        price = 0.49  # only 1 tick below
        threshold = cfg.entry_threshold_ticks * TICK_SIZE
        assert not (price <= rolling_mean - threshold)


class TestStrategyInstance:
    def test_instantiates(self) -> None:
        cfg = TickMeanRevertConfig()
        strat = TickMeanRevertStrategy(cfg)
        assert strat._cfg.strategy_id == "TICK-MEAN-REVERT-001"
        assert strat._instruments == []
        assert strat._price_history == {}
        assert strat._open_side == {}

    def test_register_before_start_queues(self) -> None:
        from nautilus_trader.model.identifiers import InstrumentId

        cfg = TickMeanRevertConfig()
        strat = TickMeanRevertStrategy(cfg)
        iid = InstrumentId.from_str("YES-TOKEN.POLYMARKET")
        strat.register_instrument(iid)
        # Should land in pending list (strategy not running yet).
        assert iid in strat._pending_instruments
        assert iid not in strat._instruments

    def test_order_size_respects_polymarket_minimum(self) -> None:
        cfg = TickMeanRevertConfig(order_size_usdc=1.0)
        strat = TickMeanRevertStrategy(cfg)
        # 1 USDC at $0.50 → 2 shares → bumped to 5 shares floor.
        size = strat._order_size(0.50)
        assert size >= 5.0


class TestTickConstant:
    def test_tick_size_is_one_cent(self) -> None:
        assert TICK_SIZE == 0.01
