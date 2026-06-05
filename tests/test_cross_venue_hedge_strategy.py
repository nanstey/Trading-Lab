from __future__ import annotations

from decimal import Decimal

from trading_lab.strategies.cross_venue_hedge import CrossVenueHedgeConfig, CrossVenueHedgeStrategy


def test_cross_venue_hedge_strategy_uses_config_driven_fair_value_model() -> None:
    cfg = CrossVenueHedgeConfig(
        poly_condition_id="0xabc",
        poly_yes_token_id="111",
        poly_no_token_id="222",
        hl_symbol="BTC",
        fair_value_anchor_price=65000.0,
        fair_value_scale=2500.0,
        fair_value_bias=0.0,
    )

    strategy = CrossVenueHedgeStrategy(cfg)

    assert strategy._hl_price_to_implied_prob(Decimal("65000")) == Decimal("0.5")
    assert strategy._hl_price_to_implied_prob(Decimal("70000")) > Decimal("0.5")


def test_cross_venue_hedge_strategy_registers_explicit_cross_venue_legs() -> None:
    cfg = CrossVenueHedgeConfig(
        poly_condition_id="0xabc",
        poly_yes_token_id="111",
        poly_no_token_id="222",
        hl_symbol="BTC",
        fair_value_anchor_price=65000.0,
        fair_value_scale=2500.0,
    )

    strategy = CrossVenueHedgeStrategy(cfg)
    strategy.register_cross_venue_legs(
        yes_instrument_id="PM-YES",
        no_instrument_id="PM-NO",
        hl_instrument_id="HL-BTC",
    )

    assert strategy._yes_instrument_id == "PM-YES"
    assert strategy._no_instrument_id == "PM-NO"
    assert strategy._hl_instrument_id == "HL-BTC"


def test_cross_venue_hedge_strategy_halts_on_failed_hl_hedge_after_pm_fill() -> None:
    cfg = CrossVenueHedgeConfig(
        poly_condition_id="0xabc",
        poly_yes_token_id="111",
        poly_no_token_id="222",
        hl_symbol="BTC",
        fair_value_anchor_price=65000.0,
        fair_value_scale=2500.0,
    )

    strategy = CrossVenueHedgeStrategy(cfg)
    strategy._legging.start_entry(direction="long_yes")
    strategy._legging.on_polymarket_fill()
    strategy.on_hyperliquid_hedge_rejected("insufficient_liquidity")

    assert strategy._legging.state == "halted"
    assert strategy._legging.needs_polymarket_flatten is True
