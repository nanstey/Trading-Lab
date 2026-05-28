"""
Tests for `HyperliquidPaperFillEngine`.

These exercise the core sweep / fill / cancel logic without spinning up a
TradingNode. The engine is subclassed to record emitted events into lists
instead of publishing them on NT's msgbus, which keeps these tests fast
and dependency-light.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import (
    ClientOrderId,
    InstrumentId,
    StrategyId,
    Symbol,
    TraderId,
    Venue,
)

from trading_lab.venues.hyperliquid.paper_fill import (
    HyperliquidPaperFillConfig,
    HyperliquidPaperFillEngine,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


HL_VENUE = Venue("HYPERLIQUID")
BTC_IID = InstrumentId(Symbol("BTC-PERP"), HL_VENUE)
TRADER_ID = TraderId("PAPER-TEST")
STRATEGY_ID = StrategyId("HL-TEST-001")


@dataclass
class _StubOrder:
    """Tiny stand-in for a NautilusTrader Order — only the fields the
    fill engine reads."""

    side: OrderSide
    price: float
    quantity: float
    time_in_force: TimeInForce
    client_order_id: ClientOrderId
    instrument_id: InstrumentId = BTC_IID
    strategy_id: StrategyId = STRATEGY_ID
    trader_id: TraderId = TRADER_ID
    order_type: int = 1  # LIMIT — value not validated by the engine


class _RecordingFillEngine(HyperliquidPaperFillEngine):
    """
    Subclass that records emitted fill / cancel events into in-memory
    lists instead of touching the NT message bus or clock. Lets us assert
    behaviour without bootstrapping a TradingNode.
    """

    def __init__(self, config: HyperliquidPaperFillConfig) -> None:
        super().__init__(config)
        self.filled: list[tuple[Any, float, float, float]] = []  # order, px, qty, fee
        self.cancelled: list[Any] = []

    def _emit_fill(self, order: Any, fill_px: float, fill_qty: float) -> None:
        fee = self._commission_usdc(fill_px, fill_qty)
        self.filled.append((order, fill_px, fill_qty, fee))

    def _emit_cancel(self, order: Any) -> None:
        self.cancelled.append(order)


def _push_touch(engine: _RecordingFillEngine, iid_str: str, bid: float, ask: float) -> None:
    """Helper: drive `_touches` + bump book seq, then sweep. Bypasses the
    delta-walking machinery."""
    engine._book_seq[iid_str] = engine._book_seq.get(iid_str, 0) + 1
    engine._touches[iid_str] = (bid, ask)
    engine._sweep(iid_str)


def _stub_order(side: OrderSide, price: float, qty: float = 1.0,
                tif: TimeInForce = TimeInForce.GTC,
                cid: str = "C1") -> _StubOrder:
    return _StubOrder(
        side=side,
        price=price,
        quantity=qty,
        time_in_force=tif,
        client_order_id=ClientOrderId(cid),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_buy_above_ask_fills_at_ask() -> None:
    """BUY at $100 with ask $98 should fill at the ask (the touch)."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig(taker_bps=0.0))
    order = _stub_order(OrderSide.BUY, price=100.0, qty=1.0, cid="B1")
    eng.register_pending(order)

    _push_touch(eng, str(BTC_IID), bid=97.0, ask=98.0)

    assert len(eng.filled) == 1
    o, px, qty, fee = eng.filled[0]
    assert o is order
    assert px == 98.0
    assert qty == 1.0
    # Order removed after fill.
    assert str(order.client_order_id) not in eng._pending


def test_buy_below_ask_does_not_fill() -> None:
    """BUY at $97 with ask $98 should remain pending."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig())
    order = _stub_order(OrderSide.BUY, price=97.0, qty=1.0, cid="B2")
    eng.register_pending(order)

    _push_touch(eng, str(BTC_IID), bid=96.0, ask=98.0)

    assert eng.filled == []
    assert str(order.client_order_id) in eng._pending


def test_sell_below_bid_fills_at_bid() -> None:
    """SELL at $50 with bid $51 fills at the bid."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig(taker_bps=0.0))
    order = _stub_order(OrderSide.SELL, price=50.0, qty=2.0, cid="S1")
    eng.register_pending(order)

    _push_touch(eng, str(BTC_IID), bid=51.0, ask=52.0)

    assert len(eng.filled) == 1
    _, px, qty, _ = eng.filled[0]
    assert px == 51.0
    assert qty == 2.0


def test_ioc_that_misses_emits_cancel_on_next_update() -> None:
    """IOC order that doesn't cross on the first book update gets cancelled."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig(ioc_max_book_updates=1))
    # Stage the book ahead of the order so register sees seq=0 and the
    # next push gives seq=1 (>= ioc_max_book_updates).
    order = _stub_order(
        OrderSide.BUY, price=10.0, qty=1.0, tif=TimeInForce.IOC, cid="I1",
    )
    eng.register_pending(order)

    # Touch with an ask well above the limit — order can't fill.
    _push_touch(eng, str(BTC_IID), bid=99.0, ask=100.0)

    assert eng.filled == []
    assert len(eng.cancelled) == 1
    assert eng.cancelled[0] is order
    # Removed from pending.
    assert str(order.client_order_id) not in eng._pending


def test_ioc_that_crosses_fills_instead_of_cancelling() -> None:
    """IOC that DOES cross on its first opportunity fills (not cancels)."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig(
        ioc_max_book_updates=1, taker_bps=0.0,
    ))
    order = _stub_order(
        OrderSide.BUY, price=100.0, qty=1.0, tif=TimeInForce.IOC, cid="I2",
    )
    eng.register_pending(order)

    _push_touch(eng, str(BTC_IID), bid=97.0, ask=98.0)

    assert len(eng.filled) == 1
    assert eng.cancelled == []


def test_taker_fee_applied_to_fill_commission() -> None:
    """A non-zero taker_bps should produce a non-zero commission proportional
    to notional. At 4.5 bps and $50k notional, fee ≈ $22.50."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig(taker_bps=4.5))
    order = _stub_order(OrderSide.BUY, price=50_000.0, qty=1.0, cid="F1")
    eng.register_pending(order)

    _push_touch(eng, str(BTC_IID), bid=49_999.0, ask=50_000.0)

    assert len(eng.filled) == 1
    _, fill_px, fill_qty, fee = eng.filled[0]
    notional = fill_px * fill_qty
    expected_fee = notional * (4.5 / 10_000.0)
    assert fee == pytest.approx(expected_fee, rel=1e-9)
    assert fee == pytest.approx(22.5, abs=1e-6)


def test_zero_taker_fee_produces_zero_commission() -> None:
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig(taker_bps=0.0))
    order = _stub_order(OrderSide.SELL, price=100.0, qty=3.0, cid="F2")
    eng.register_pending(order)

    _push_touch(eng, str(BTC_IID), bid=101.0, ask=102.0)

    _, _, _, fee = eng.filled[0]
    assert fee == 0.0


def test_cancel_pending_emits_cancel_event() -> None:
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig())
    order = _stub_order(OrderSide.BUY, price=10.0, qty=1.0, cid="X1")
    eng.register_pending(order)

    eng.cancel_pending(str(order.client_order_id))

    assert eng.cancelled == [order]
    assert str(order.client_order_id) not in eng._pending


def test_one_sided_touch_does_not_match_opposite_side() -> None:
    """If only bid is known (ask=None) a BUY order shouldn't fill, and vice versa."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig())
    buy = _stub_order(OrderSide.BUY, price=100.0, qty=1.0, cid="OB")
    sell = _stub_order(OrderSide.SELL, price=100.0, qty=1.0, cid="OS")
    eng.register_pending(buy)
    eng.register_pending(sell)

    # Only the bid is known.
    _push_touch(eng, str(BTC_IID), bid=99.0, ask=None)  # type: ignore[arg-type]

    # BUY needs an ask <= 100 — no ask, no fill.
    # SELL needs bid >= 100 — bid is 99, no fill.
    assert eng.filled == []


def test_multiple_orders_sweep_independently() -> None:
    """Two orders on the same instrument; only the crossing one fills."""
    eng = _RecordingFillEngine(HyperliquidPaperFillConfig(taker_bps=0.0))
    crossing = _stub_order(OrderSide.BUY, price=100.0, qty=1.0, cid="A")
    far = _stub_order(OrderSide.BUY, price=10.0, qty=1.0, cid="B")
    eng.register_pending(crossing)
    eng.register_pending(far)

    _push_touch(eng, str(BTC_IID), bid=97.0, ask=98.0)

    assert len(eng.filled) == 1
    filled_order, _, _, _ = eng.filled[0]
    assert filled_order is crossing
    assert str(far.client_order_id) in eng._pending
