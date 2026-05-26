"""Tests for `agent/venue_equity` — focus on the static/test helper
and the snapshot dataclass shape. Network paths covered by integration
tests when creds are available."""

from __future__ import annotations

import pytest

from nautilus_predict.agent.venue_equity import (
    EquitySnapshot,
    PolymarketEquityProvider,
    StaticEquityProvider,
)


def test_static_provider_returns_set_value():
    p = StaticEquityProvider(total_usdc=1234.56)
    assert p.current_usdc() == 1234.56
    assert p.snapshot.source == "static"


def test_static_provider_set_value_mutates():
    p = StaticEquityProvider(total_usdc=100.0)
    p.set_value(500.0)
    assert p.current_usdc() == 500.0


@pytest.mark.asyncio
async def test_static_refresh_is_noop_but_returns_snapshot():
    p = StaticEquityProvider(total_usdc=42.0)
    snap = await p.refresh()
    assert isinstance(snap, EquitySnapshot)
    assert snap.total_usdc == 42.0


def test_pm_provider_uncached_is_zero():
    p = PolymarketEquityProvider(wallet_address="0xabc")
    assert p.current_usdc() == 0.0
    assert p.snapshot is None
    assert p.age_seconds() is None


@pytest.mark.asyncio
async def test_pm_provider_fallback_path_returns_zero_snapshot_when_no_rest():
    """No rest_client + no wallet → data-api fails, clob fails, returns fallback."""
    p = PolymarketEquityProvider(wallet_address="")
    snap = await p.refresh()
    assert snap.source == "fallback"
    assert snap.total_usdc == 0.0
