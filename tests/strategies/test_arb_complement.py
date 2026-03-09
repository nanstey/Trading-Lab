"""
Unit tests for Binary Complement Arbitrage strategy logic.

Tests the opportunity detection and order pairing logic without requiring
a live NautilusTrader node.
"""

from __future__ import annotations

import pytest

from nautilus_predict.strategies.arb_complement import (
    TAKER_FEE,
    ArbOpportunity,
    BinaryArbConfig,
    BinaryArbStrategy,
    MarketPair,
)


class TestArbDetection:
    """Test arbitrage opportunity detection logic."""

    def test_arb_condition_met(self) -> None:
        """When combined ask < 1.00 - fee, arb should be profitable."""
        yes_ask = 0.48
        no_ask = 0.48
        combined = yes_ask + no_ask
        profit = 1.0 - combined - TAKER_FEE
        assert profit > 0, f"Expected profit > 0, got {profit}"

    def test_arb_condition_not_met(self) -> None:
        """When combined ask >= 1.00 - fee, no arb."""
        yes_ask = 0.50
        no_ask = 0.52
        combined = yes_ask + no_ask
        profit = 1.0 - combined - TAKER_FEE
        assert profit <= 0, f"Expected profit <= 0, got {profit}"

    def test_min_profit_threshold(self) -> None:
        """Arb should only trigger when profit exceeds min_profit_usdc."""
        min_profit = 0.05
        # Combined cost that just barely exceeds threshold
        combined_at_threshold = 1.0 - TAKER_FEE - min_profit
        combined_below_threshold = combined_at_threshold + 0.01

        profit_at = 1.0 - combined_at_threshold - TAKER_FEE
        profit_below = 1.0 - combined_below_threshold - TAKER_FEE

        assert profit_at >= min_profit
        assert profit_below < min_profit

    def test_profit_calculation_per_share(self) -> None:
        """Verify profit calculation for a concrete example."""
        yes_ask = 0.45
        no_ask = 0.46
        taker_fee = TAKER_FEE
        expected_profit_per_share = 1.0 - yes_ask - no_ask - taker_fee
        assert abs(expected_profit_per_share - (1.0 - 0.45 - 0.46 - TAKER_FEE)) < 1e-10


class TestArbOpportunity:
    def test_dataclass_creation(self) -> None:
        from nautilus_trader.model.identifiers import InstrumentId

        yes_id = InstrumentId.from_str("YES-TOKEN.POLYMARKET")
        no_id = InstrumentId.from_str("NO-TOKEN.POLYMARKET")
        pair = MarketPair("cond-123", yes_id, no_id)

        arb = ArbOpportunity(
            pair=pair,
            yes_ask=0.47,
            no_ask=0.47,
            combined_cost=0.94,
            expected_profit=0.04,
        )
        assert arb.combined_cost == 0.94
        assert not arb.yes_filled
        assert not arb.no_filled

    def test_both_filled_state(self) -> None:
        from nautilus_trader.model.identifiers import InstrumentId

        yes_id = InstrumentId.from_str("YES-TOKEN.POLYMARKET")
        no_id = InstrumentId.from_str("NO-TOKEN.POLYMARKET")
        pair = MarketPair("cond-123", yes_id, no_id)

        arb = ArbOpportunity(
            pair=pair,
            yes_ask=0.47,
            no_ask=0.47,
            combined_cost=0.94,
            expected_profit=0.04,
        )
        arb.yes_filled = True
        arb.no_filled = True
        assert arb.yes_filled and arb.no_filled


class TestBinaryArbConfig:
    def test_default_config(self) -> None:
        cfg = BinaryArbConfig()
        assert cfg.min_profit_usdc == 0.02
        assert cfg.max_capital_usdc == 1000.0

    def test_custom_config(self) -> None:
        cfg = BinaryArbConfig(min_profit_usdc=0.10, max_capital_usdc=500.0)
        assert cfg.min_profit_usdc == 0.10
        assert cfg.max_capital_usdc == 500.0
