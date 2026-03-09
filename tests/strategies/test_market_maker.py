"""
Unit tests for MarketMaking strategy parameters and quote logic.

Tests the pure calculation logic (spread, skew, clamping) independently
of NautilusTrader runtime infrastructure.
"""

from __future__ import annotations

import pytest

from nautilus_predict.strategies.market_maker import MarketMakingConfig, MarketMakingStrategy


class TestMarketMakingConfig:
    def test_default_values(self) -> None:
        cfg = MarketMakingConfig()
        assert cfg.spread_bps == 50
        assert cfg.order_size_usdc == 10.0
        assert cfg.max_position_usdc == 500.0

    def test_custom_values(self) -> None:
        cfg = MarketMakingConfig(spread_bps=100, order_size_usdc=25.0)
        assert cfg.spread_bps == 100
        assert cfg.order_size_usdc == 25.0


class TestQuotePriceLogic:
    """Test the spread and skew calculations directly."""

    def _compute_quotes(
        self,
        mid: float,
        spread_bps: int,
        inventory_usdc: float = 0.0,
        max_position_usdc: float = 500.0,
        skew_factor: float = 0.1,
    ) -> tuple[float, float]:
        """
        Replicate the core quote computation from MarketMakingStrategy.
        Returns (bid_price, ask_price).
        """
        half_spread = mid * spread_bps / 10_000
        skew = skew_factor * inventory_usdc / max_position_usdc
        skewed_mid = mid - skew

        new_bid = round(skewed_mid - half_spread, 4)
        new_ask = round(skewed_mid + half_spread, 4)

        # Clamp to valid binary range
        new_bid = max(0.01, min(new_bid, 0.98))
        new_ask = max(new_bid + 0.01, min(new_ask, 0.99))
        return new_bid, new_ask

    def test_symmetric_spread_around_mid(self) -> None:
        bid, ask = self._compute_quotes(mid=0.50, spread_bps=100)
        assert ask > bid
        assert abs((ask + bid) / 2 - 0.50) < 1e-6

    def test_spread_is_at_least_min_spread(self) -> None:
        bid, ask = self._compute_quotes(mid=0.50, spread_bps=50)
        actual_spread_bps = (ask - bid) / 0.50 * 10_000
        assert actual_spread_bps >= 50 - 1  # allow 1bps float tolerance

    def test_bid_clamped_above_001(self) -> None:
        # Very low mid should not produce negative or zero bid
        bid, ask = self._compute_quotes(mid=0.02, spread_bps=200)
        assert bid >= 0.01

    def test_ask_clamped_below_099(self) -> None:
        # Very high mid should not produce ask above 0.99
        bid, ask = self._compute_quotes(mid=0.98, spread_bps=200)
        assert ask <= 0.99

    def test_positive_inventory_skews_bid_down(self) -> None:
        """Long inventory → lower bid to discourage more buying."""
        bid_no_inv, ask_no_inv = self._compute_quotes(mid=0.50, spread_bps=100)
        bid_long, ask_long = self._compute_quotes(
            mid=0.50, spread_bps=100, inventory_usdc=200.0
        )
        assert bid_long < bid_no_inv
        assert ask_long < ask_no_inv

    def test_negative_inventory_skews_bid_up(self) -> None:
        """Short inventory → higher bid to encourage buying."""
        bid_no_inv, _ = self._compute_quotes(mid=0.50, spread_bps=100)
        bid_short, _ = self._compute_quotes(
            mid=0.50, spread_bps=100, inventory_usdc=-200.0
        )
        assert bid_short > bid_no_inv
