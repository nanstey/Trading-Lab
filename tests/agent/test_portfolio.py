"""Unit tests for the per-strategy `PortfolioAllocator`.

Covers:
  - parse_cap (absolute / pct / mixed forms)
  - Cap enforcement (BUY under/over, SELL close, SELL naked-short)
  - Pct-of-equity caps that adapt as venue equity grows/shrinks
  - Snapshot fields shape
  - Failure modes (no portfolio attached, exception in net_exposures,
    pct cap with zero equity)
  - `for_slug` factory: explicit pct, explicit absolute, fair-share fallback,
    legacy fallback
  - `validate_allocations` warnings (pct oversum, absolute oversum, invalid)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from nautilus_predict.agent.portfolio import (
    AllocatorDecision,
    CapSpec,
    PortfolioAllocator,
    for_slug,
    parse_cap,
    validate_allocations,
)
from nautilus_predict.agent.venue_equity import StaticEquityProvider


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeOrder:
    side: object
    quantity: float = 100.0
    price: float = 0.5
    instrument_id: object = "TOKEN.POLYMARKET"


@dataclass
class _FakeMoney:
    val: float

    def __float__(self):
        return float(self.val)


@dataclass
class _FakePortfolio:
    exposures_by_venue: dict = field(default_factory=dict)
    positions: dict = field(default_factory=dict)
    raise_on_exposures: bool = False

    def net_exposures(self, venue):
        if self.raise_on_exposures:
            raise RuntimeError("simulated portfolio failure")
        return self.exposures_by_venue.get(str(venue))

    def net_position(self, instrument_id):
        return self.positions.get(instrument_id, 0)


class _USDC:
    code = "USDC"


def _buy_side():
    from nautilus_trader.model.enums import OrderSide
    return OrderSide.BUY


def _sell_side():
    from nautilus_trader.model.enums import OrderSide
    return OrderSide.SELL


def _make_alloc_absolute(cap_usdc: float = 100.0, *, portfolio_open: float = 0.0):
    a = PortfolioAllocator(slug="s", cap=CapSpec(absolute_usdc=cap_usdc))
    a.set_portfolio(_FakePortfolio(
        exposures_by_venue={"POLYMARKET": {_USDC(): _FakeMoney(portfolio_open)}},
    ))
    return a


def _make_alloc_pct(pct: float, equity_usdc: float, *, portfolio_open: float = 0.0):
    eq = StaticEquityProvider(total_usdc=equity_usdc)
    a = PortfolioAllocator(
        slug="s", cap=CapSpec(pct_of_equity=pct), equity_provider=eq,
    )
    a.set_portfolio(_FakePortfolio(
        exposures_by_venue={"POLYMARKET": {_USDC(): _FakeMoney(portfolio_open)}},
    ))
    return a, eq


# ---------------------------------------------------------------------------
# parse_cap
# ---------------------------------------------------------------------------


def test_parse_cap_absolute_float():
    spec = parse_cap(400.0)
    assert spec.absolute_usdc == 400.0 and spec.pct_of_equity is None


def test_parse_cap_absolute_int():
    spec = parse_cap(400)
    assert spec.absolute_usdc == 400.0


def test_parse_cap_pct_float_lt_one():
    spec = parse_cap(0.4)
    assert spec.pct_of_equity == pytest.approx(0.4)
    assert spec.absolute_usdc is None


def test_parse_cap_pct_one_is_pct():
    spec = parse_cap(1.0)
    assert spec.pct_of_equity == 1.0


def test_parse_cap_pct_string():
    spec = parse_cap("40%")
    assert spec.pct_of_equity == pytest.approx(0.4)


def test_parse_cap_string_absolute():
    spec = parse_cap("250")
    assert spec.absolute_usdc == 250.0


def test_parse_cap_pct_over_100_rejected():
    with pytest.raises(ValueError):
        parse_cap("150%")


def test_parse_cap_negative_rejected():
    with pytest.raises(ValueError):
        parse_cap(-10)


def test_parse_cap_bool_rejected():
    with pytest.raises(ValueError):
        parse_cap(True)


# ---------------------------------------------------------------------------
# Absolute cap — sanity (regression for v1 path)
# ---------------------------------------------------------------------------


def test_absolute_buy_under_cap_accepted():
    a = _make_alloc_absolute(100.0)
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=100, price=0.5))
    assert d.accepted and d.cap_usdc == 100.0


def test_absolute_buy_over_cap_rejected():
    a = _make_alloc_absolute(100.0, portfolio_open=80.0)
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=100, price=0.5))
    assert not d.accepted and "cap exceeded" in d.reason


def test_sell_closing_position_accepted_at_cap():
    a = PortfolioAllocator(slug="s", cap=CapSpec(absolute_usdc=100.0))
    a.set_portfolio(_FakePortfolio(
        exposures_by_venue={"POLYMARKET": {_USDC(): _FakeMoney(100)}},
        positions={"TOKEN.POLYMARKET": 100},
    ))
    d = a.check_order(_FakeOrder(side=_sell_side(), quantity=50, price=0.6))
    assert d.accepted and d.reason == "closing position"


# ---------------------------------------------------------------------------
# Pct-of-equity caps
# ---------------------------------------------------------------------------


def test_pct_cap_resolves_against_equity():
    a, eq = _make_alloc_pct(pct=0.4, equity_usdc=1000.0)
    assert a.cap_usdc == pytest.approx(400.0)
    eq.set_value(2000.0)
    assert a.cap_usdc == pytest.approx(800.0)


def test_pct_cap_grows_with_equity():
    a, eq = _make_alloc_pct(pct=0.25, equity_usdc=400.0)
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=100, price=1.5))
    assert not d.accepted  # 150 > 25% of 400 = 100

    eq.set_value(1000.0)  # account doubled-and-a-half — cap is now $250
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=100, price=1.5))
    assert d.accepted


def test_pct_cap_shrinks_with_equity():
    a, eq = _make_alloc_pct(pct=0.5, equity_usdc=1000.0)
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=100, price=4.0))
    assert d.accepted  # 400 < 50% of 1000 = 500

    eq.set_value(500.0)  # account halved — cap is now $250
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=100, price=4.0))
    assert not d.accepted  # 400 > 250


def test_pct_cap_with_zero_equity_blocks_with_clear_reason():
    a = PortfolioAllocator(
        slug="s", cap=CapSpec(pct_of_equity=0.5),
        equity_provider=StaticEquityProvider(total_usdc=0),
    )
    a.set_portfolio(_FakePortfolio(exposures_by_venue={"POLYMARKET": {_USDC(): _FakeMoney(0)}}))
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=10, price=0.5))
    assert not d.accepted
    assert "cap unresolved" in d.reason or "equity provider" in d.reason


def test_pct_cap_construction_requires_equity_provider():
    with pytest.raises(ValueError):
        PortfolioAllocator(slug="s", cap=CapSpec(pct_of_equity=0.5))


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_absolute_cap_shape():
    a = _make_alloc_absolute(200.0, portfolio_open=50.0)
    snap = a.snapshot()
    assert snap["cap_spec"] == "$200.00"
    assert snap["is_pct"] is False
    assert snap["cap_usdc"] == 200.0
    assert snap["venue_equity_usdc"] is None
    assert snap["open_notional_usdc"] == 50.0
    assert snap["utilisation_pct"] == 25.0


def test_snapshot_pct_cap_shape():
    a, _ = _make_alloc_pct(pct=0.3, equity_usdc=2000.0, portfolio_open=150.0)
    snap = a.snapshot()
    assert snap["is_pct"] is True
    assert "30.00% of venue equity" in snap["cap_spec"]
    assert snap["cap_usdc"] == pytest.approx(600.0)
    assert snap["venue_equity_usdc"] == 2000.0
    assert snap["utilisation_pct"] == 25.0


def test_exposure_extraction_fails_open():
    a = PortfolioAllocator(slug="s", cap=CapSpec(absolute_usdc=100.0))
    a.set_portfolio(_FakePortfolio(raise_on_exposures=True))
    assert a.open_notional_usdc == 0.0
    d = a.check_order(_FakeOrder(side=_buy_side(), quantity=10, price=0.5))
    assert d.accepted


# ---------------------------------------------------------------------------
# Factory: for_slug
# ---------------------------------------------------------------------------


def _make_cfg(allocations=None, max_total=1000.0, max_pos=100.0):
    risk = SimpleNamespace(
        max_position_usdc=max_pos,
        max_total_exposure_usdc=max_total,
        daily_loss_limit_usdc=-200.0,
    )
    portfolio = SimpleNamespace(risk=risk, allocations=allocations or {})
    return SimpleNamespace(portfolio=portfolio)


def test_for_slug_explicit_absolute():
    cfg = _make_cfg(allocations={"foo": 250.0})
    a = for_slug("foo", cfg)
    assert a.cap_usdc == 250.0 and not a.cap_spec.is_pct


def test_for_slug_explicit_pct_with_equity_provider():
    cfg = _make_cfg(allocations={"foo": "40%"})
    eq = StaticEquityProvider(total_usdc=500.0)
    a = for_slug("foo", cfg, equity_provider=eq)
    assert a.cap_spec.is_pct
    assert a.cap_usdc == pytest.approx(200.0)


def test_for_slug_explicit_pct_without_equity_raises():
    cfg = _make_cfg(allocations={"foo": "40%"})
    with pytest.raises(ValueError):
        for_slug("foo", cfg)


def test_for_slug_fair_share_when_unspecified(monkeypatch):
    import nautilus_predict.agent.lifecycle as lc
    monkeypatch.setattr(
        lc, "list_hypotheses",
        lambda state=None, **kw: [object()] * 2,
    )
    cfg = _make_cfg(allocations={}, max_total=1000.0)
    a = for_slug("unknown", cfg)
    assert a.cap_usdc == pytest.approx(250.0)
    assert not a.cap_spec.is_pct  # fair-share is always absolute


def test_for_slug_legacy_fallback():
    cfg = _make_cfg(allocations={}, max_total=0.0, max_pos=42.0)
    a = for_slug("unknown", cfg)
    assert a.cap_usdc == 42.0


# ---------------------------------------------------------------------------
# validate_allocations
# ---------------------------------------------------------------------------


def test_validate_warns_on_absolute_oversum():
    cfg = _make_cfg(allocations={"a": 600.0, "b": 500.0}, max_total=1000.0)
    warnings = validate_allocations(cfg)
    assert any("absolute" in w and "over-committed" in w for w in warnings)


def test_validate_warns_on_pct_oversum():
    cfg = _make_cfg(allocations={"a": "60%", "b": "50%"}, max_total=1000.0)
    warnings = validate_allocations(cfg)
    assert any("pct" in w and "100%" in w for w in warnings)


def test_validate_mixed_under_limit_is_clean():
    cfg = _make_cfg(allocations={"a": 300.0, "b": "40%"}, max_total=1000.0)
    assert validate_allocations(cfg) == []


def test_validate_invalid_entry_emits_warning():
    cfg = _make_cfg(allocations={"a": -10.0}, max_total=1000.0)
    warnings = validate_allocations(cfg)
    assert any("a" in w and "must be > 0" in w for w in warnings)


def test_validate_invalid_pct_emits_warning():
    cfg = _make_cfg(allocations={"a": "150%"}, max_total=1000.0)
    warnings = validate_allocations(cfg)
    assert any("a" in w for w in warnings)


# ---------------------------------------------------------------------------
# AllocatorDecision dataclass
# ---------------------------------------------------------------------------


def test_decision_default_factory():
    d = AllocatorDecision(accepted=True)
    assert d.reason == "" and d.cap_usdc == 0.0
