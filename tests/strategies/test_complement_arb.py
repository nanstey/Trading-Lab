"""
Unit tests for ComplementArbStrategy.

Tests the core arbitrage opportunity detection logic without requiring
a live NautilusTrader node or API credentials. The check_arb_opportunity
method is pure business logic that can be tested in isolation.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from nautilus_predict.strategies.complement_arb import ComplementArbStrategy


def make_strategy(min_profit_usdc: float = 0.0) -> ComplementArbStrategy:
    """Create a ComplementArbStrategy instance with mock config for testing."""
    mock_config = MagicMock()
    mock_config.is_live = False
    mock_config.is_paper = True
    mock_config.trading_mode.value = "paper"
    return ComplementArbStrategy(
        condition_id="0xtest",
        yes_token_id="0xyes",
        no_token_id="0xno",
        min_profit_usdc=min_profit_usdc,
        max_order_size_usdc=100.0,
        config=mock_config,
        kill_switch=None,
    )


class TestCheckArbOpportunity:
    """Test the check_arb_opportunity method directly."""

    def test_profitable_arb_detected(self) -> None:
        """
        YES ask = 0.60 + NO ask = 0.39 = 0.99 < 1.00.
        Profit = 0.01 USDC per pair. Should return True.
        """
        strategy = make_strategy(min_profit_usdc=0.005)
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.60"),
            no_ask=Decimal("0.39"),
        )
        assert result is True, (
            "Expected arb opportunity: YES=0.60 + NO=0.39 = 0.99, profit = 0.01"
        )

    def test_no_arb_when_combined_over_one(self) -> None:
        """
        YES ask = 0.55 + NO ask = 0.46 = 1.01 > 1.00.
        No profit possible. Should return False.
        """
        strategy = make_strategy(min_profit_usdc=0.005)
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.55"),
            no_ask=Decimal("0.46"),
        )
        assert result is False, (
            "Expected no arb: YES=0.55 + NO=0.46 = 1.01, no profit"
        )

    def test_no_arb_when_combined_exactly_one(self) -> None:
        """
        YES ask = 0.50 + NO ask = 0.50 = 1.00.
        Exactly 1.00 means zero profit - not profitable. Should return False.
        """
        strategy = make_strategy(min_profit_usdc=0.0)
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.50"),
            no_ask=Decimal("0.50"),
        )
        assert result is False, (
            "Expected no arb: YES=0.50 + NO=0.50 = 1.00 exactly, zero profit"
        )

    def test_large_arb_opportunity(self) -> None:
        """Large mispricing (both tokens at 0.40) should always trigger."""
        strategy = make_strategy(min_profit_usdc=0.005)
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.40"),
        )
        assert result is True, (
            "Expected arb: YES=0.40 + NO=0.40 = 0.80, profit = 0.20"
        )

    def test_min_profit_threshold_filters_marginal_arbs(self) -> None:
        """
        Combined = 0.995, profit = 0.005.
        With min_profit=0.005, it should not trigger (profit == min_profit, not >).
        """
        strategy = make_strategy(min_profit_usdc=0.005)
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.500"),
            no_ask=Decimal("0.495"),
        )
        # profit = 1.00 - 0.500 - 0.495 = 0.005 == min_profit
        # check_arb_opportunity requires profit > min_profit (strictly)
        assert result is False, (
            "Marginal arb at exactly min_profit threshold should not trigger"
        )

    def test_arb_above_min_profit_threshold(self) -> None:
        """Profit strictly above min_profit threshold should trigger."""
        strategy = make_strategy(min_profit_usdc=0.005)
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.490"),
            no_ask=Decimal("0.490"),
        )
        # profit = 1.00 - 0.490 - 0.490 = 0.020 > 0.005
        assert result is True, (
            "Expected arb: profit 0.020 > min_profit 0.005"
        )

    def test_high_min_profit_filters_small_arbs(self) -> None:
        """With a high min_profit, small arbs should be filtered."""
        strategy = make_strategy(min_profit_usdc=0.05)
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.60"),
            no_ask=Decimal("0.39"),
        )
        # profit = 0.01 < min_profit = 0.05
        assert result is False, (
            "Small arb (profit=0.01) should be filtered by min_profit=0.05"
        )

    def test_decimal_precision(self) -> None:
        """Test that Decimal arithmetic avoids floating-point precision issues."""
        strategy = make_strategy(min_profit_usdc=0.001)
        # Test with values that cause floating-point issues: 0.1 + 0.2 != 0.3 in float
        result = strategy.check_arb_opportunity(
            yes_ask=Decimal("0.1"),
            no_ask=Decimal("0.2"),
        )
        # 0.1 + 0.2 = 0.3, profit = 0.7, which is >> min_profit
        assert result is True, "Decimal arithmetic should handle 0.1 + 0.2 correctly"
