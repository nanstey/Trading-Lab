from __future__ import annotations

from trading_lab.strategies.cross_venue_state import CrossVenueLeggingStateMachine


def test_legging_state_machine_advances_pm_fill_to_hedged() -> None:
    sm = CrossVenueLeggingStateMachine()

    sm.start_entry(direction="long_yes")
    sm.on_polymarket_fill()
    sm.on_hyperliquid_fill()

    assert sm.state == "hedged"
    assert sm.needs_polymarket_flatten is False


def test_legging_state_machine_halts_when_hedge_fails_after_pm_fill() -> None:
    sm = CrossVenueLeggingStateMachine()

    sm.start_entry(direction="long_yes")
    sm.on_polymarket_fill()
    sm.on_hyperliquid_reject(reason="insufficient_liquidity")

    assert sm.state == "halted"
    assert sm.needs_polymarket_flatten is True
    assert sm.last_reason == "insufficient_liquidity"


def test_legging_state_machine_forced_flatten_clears_halted_exposure() -> None:
    sm = CrossVenueLeggingStateMachine()

    sm.start_entry(direction="long_yes")
    sm.on_polymarket_fill()
    sm.on_hyperliquid_reject(reason="insufficient_liquidity")
    sm.on_polymarket_flattened()

    assert sm.state == "flat"
    assert sm.needs_polymarket_flatten is False
