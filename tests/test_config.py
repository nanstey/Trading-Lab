"""
Unit tests for system configuration loading and validation.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from nautilus_predict.config import (
    ArbConfig,
    HedgeConfig,
    MarketMakerConfig,
    PolymarketConfig,
    SystemConfig,
    TradingConfig,
    TradingMode,
)


class TestTradingMode:
    def test_values(self) -> None:
        assert TradingMode.LIVE == "live"
        assert TradingMode.PAPER == "paper"
        assert TradingMode.BACKTEST == "backtest"


class TestPolymarketConfig:
    def test_private_key_strip_0x(self) -> None:
        with patch.dict(os.environ, {"POLY_PRIVATE_KEY": "0x" + "deadbeef" * 8}):
            cfg = PolymarketConfig()
            assert not cfg.private_key.get_secret_value().startswith("0x")

    def test_private_key_no_prefix_unchanged(self) -> None:
        raw = "deadbeef" * 8
        with patch.dict(os.environ, {"POLY_PRIVATE_KEY": raw}):
            cfg = PolymarketConfig()
            assert cfg.private_key.get_secret_value() == raw

    def test_default_urls(self) -> None:
        cfg = PolymarketConfig()
        assert "polymarket.com" in cfg.host
        assert "ws" in cfg.ws_host


class TestMarketMakerConfig:
    def test_defaults(self) -> None:
        cfg = MarketMakerConfig()
        assert cfg.spread_bps == 50
        assert cfg.order_size_usdc == 10.0
        assert cfg.max_position_usdc == 500.0


class TestArbConfig:
    def test_defaults(self) -> None:
        cfg = ArbConfig()
        assert cfg.min_profit_usdc == 0.02
        assert cfg.max_capital_usdc == 1000.0


class TestHedgeConfig:
    def test_defaults(self) -> None:
        cfg = HedgeConfig()
        assert 0.0 <= cfg.ratio <= 1.0
        assert cfg.instrument  # non-empty

    def test_ratio_bounds(self) -> None:
        with patch.dict(os.environ, {"HEDGE_RATIO": "0.75"}):
            cfg = HedgeConfig()
            assert cfg.ratio == 0.75


class TestSystemConfig:
    def test_default_mode(self) -> None:
        cfg = SystemConfig()
        assert cfg.trading_mode == TradingMode.PAPER

    def test_is_live_false_for_paper(self) -> None:
        cfg = SystemConfig()
        assert not cfg.is_live

    def test_is_live_true_for_live(self) -> None:
        with patch.dict(os.environ, {"TRADING_MODE": "live", "LIVE_TRADING_CONFIRMED": "true"}):
            cfg = TradingConfig()
            assert cfg.is_live
