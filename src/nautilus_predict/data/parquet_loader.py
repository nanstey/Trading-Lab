"""
Parquet → NautilusTrader adapter.

Bridges the project's PyArrow-backed `DataCatalog` and the NautilusTrader
`BacktestEngine`. Exposes helpers to materialise `TradeTick` and
`OrderBookDelta` event lists plus the venue-side `BettingInstrument`
needed to register a Polymarket binary token with the engine.

Why a `BettingInstrument`?
    Polymarket binary tokens are probability-priced (0–1 range, $1 payout at
    resolution). NautilusTrader's `BettingInstrument` is the canonical type
    for this — preserves the right precision and notional semantics.

Reconstructed-book caveat
    Polymarket has no historical book endpoint. For backtests we either
    (a) replay forward-captured snapshots, or (b) reconstruct a coarse
    best-bid/best-ask track from trade prints under the assumption that
    each trade was a taker against the displayed best on its side. The
    reconstruction is intentionally pessimistic about depth; backtest fill
    realism is handled separately by `FillModel`/`LatencyModel`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import (
    BookOrder,
    OrderBookDelta,
    OrderBookDeltas,
    TradeTick,
)
from nautilus_trader.model.enums import AggressorSide, BookAction, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.instruments import BettingInstrument
from nautilus_trader.model.objects import Money

if TYPE_CHECKING:
    from nautilus_predict.data.catalog import DataCatalog

log = logging.getLogger(__name__)

POLYMARKET_VENUE = Venue("POLYMARKET")


def _short_token(token_id: str) -> str:
    """First 24 decimal digits of the token_id (enough uniqueness for a Symbol)."""
    return token_id.lstrip("0x").lstrip("0X")[:24]


def make_instrument_id(token_id: str) -> InstrumentId:
    """Build a stable NautilusTrader `InstrumentId` for a PM token."""
    return InstrumentId(Symbol(_short_token(token_id)), POLYMARKET_VENUE)


def make_instrument(
    token_id: str,
    condition_id: str,
    question: str = "",
    end_date_iso: str = "",
) -> BettingInstrument:
    """
    Build a NautilusTrader `BettingInstrument` descriptor for a PM token.

    Polymarket binary tokens trade at a minimum tick of $0.01 and a minimum
    order size of $5 USDC notional. We model them under `betting_type="ODDS"`
    with `currency=USDC` so the engine's account ledger speaks the right
    currency end-to-end.
    """
    short = _short_token(token_id)
    # selection_id / event_id must fit in a C long (32-bit on some platforms).
    # Hash to a deterministic 31-bit positive int.
    import hashlib

    def _stable_int31(s: str) -> int:
        h = hashlib.sha1(s.encode()).digest()
        return int.from_bytes(h[:4], "big") & 0x7FFFFFFF

    selection_id = _stable_int31(short or token_id)
    event_id = _stable_int31(condition_id or "POLYMARKET")
    # BettingInstrument synthesises its Symbol from
    #   f"{market_id}-{selection_id}-{selection_handicap}". Keep market_id short.
    market_id_short = (
        condition_id[2:14] if condition_id.startswith("0x") else (condition_id or "PM")
    )[:16]
    return BettingInstrument(
        venue_name="POLYMARKET",
        betting_type="ODDS",
        competition_id=0,
        competition_name="POLYMARKET",
        event_country_code="US",
        event_id=event_id,
        event_name=(question or "PolymarketEvent")[:60],
        event_open_date=pd.Timestamp.fromtimestamp(0, tz="UTC"),
        event_type_id=1,
        event_type_name="PredictionMarket",
        market_id=market_id_short,
        market_name=(question or "PolymarketMarket")[:60],
        market_start_time=pd.Timestamp(end_date_iso) if end_date_iso else pd.Timestamp.fromtimestamp(0, tz="UTC"),
        market_type="BINARY",
        selection_handicap=0.0,
        selection_id=selection_id,
        selection_name=short,
        currency="USDC",
        price_precision=2,
        size_precision=2,
        min_notional=Money(5, USDC),
        ts_event=0,
        ts_init=0,
    )


def load_trades_as_trade_ticks(
    catalog: DataCatalog,
    token_id: str,
    instrument: BettingInstrument,
    start: datetime,
    end: datetime,
) -> list[TradeTick]:
    """Read trades from the catalog and convert to `TradeTick` events."""
    df = catalog.read_trades(token_id, start, end)
    if df.empty:
        return []

    ticks: list[TradeTick] = []
    for i, row in enumerate(df.itertuples(index=False)):
        ts_ms = int(row.timestamp)
        ts_ns = ts_ms * 1_000_000
        price = float(row.price)
        # Polymarket prices occasionally come back with extra precision
        # (e.g., 0.38999991...). Clamp to the instrument's tick grid.
        price = max(0.01, min(0.99, round(price, 2)))
        size = max(float(instrument.min_quantity or 0.01), float(row.size))

        side_str = (row.side or "").upper()
        aggressor = (
            AggressorSide.BUYER
            if side_str == "BUY"
            else AggressorSide.SELLER
            if side_str == "SELL"
            else AggressorSide.NO_AGGRESSOR
        )

        # TradeId max length is 36 chars; tx hashes are 66. Truncate the
        # leading 0x and take a unique prefix.
        raw_id = str(row.trade_id or f"T{i}")
        tid = raw_id[2:34] if raw_id.startswith("0x") else raw_id[:32]
        trade_id = TradeId(tid or f"T{i}")
        ticks.append(
            TradeTick(
                instrument_id=instrument.id,
                price=instrument.make_price(price),
                size=instrument.make_qty(size),
                aggressor_side=aggressor,
                trade_id=trade_id,
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
        )
    return ticks


def load_book_as_order_book_deltas(
    catalog: DataCatalog,
    token_id: str,
    instrument: BettingInstrument,
    start: datetime,
    end: datetime,
) -> list[OrderBookDeltas]:
    """Read snapshot rows and convert each timestamp group to `OrderBookDeltas`."""
    df = catalog.read_orderbook_history(token_id, start, end)
    if df.empty:
        return []

    deltas_out: list[OrderBookDeltas] = []
    grouped = df.groupby("timestamp", sort=True)
    for ts_ms, group in grouped:
        ts_ns = int(ts_ms) * 1_000_000
        deltas: list[OrderBookDelta] = [
            OrderBookDelta.clear(instrument.id, 0, ts_ns, ts_ns)
        ]
        for row in group.itertuples(index=False):
            price = max(0.01, min(0.99, round(float(row.price), 2)))
            size = float(row.size)
            if size <= 0:
                continue
            side = OrderSide.BUY if row.side == "bid" else OrderSide.SELL
            order = BookOrder(
                side,
                instrument.make_price(price),
                instrument.make_qty(size),
                0,
            )
            deltas.append(
                OrderBookDelta(
                    instrument.id, BookAction.ADD, order, 0, 0, ts_ns, ts_ns
                )
            )
        if len(deltas) > 1:
            deltas_out.append(
                OrderBookDeltas(instrument_id=instrument.id, deltas=deltas)
            )
    return deltas_out


def reconstruct_book_from_trades(
    catalog: DataCatalog,
    token_id: str,
    instrument: BettingInstrument,
    start: datetime,
    end: datetime,
    depth_per_side: float = 100.0,
) -> list[OrderBookDeltas]:
    """
    Reconstruct a coarse book from trade prints when no real snapshots exist.

    Assumption: each trade's print price is the best on its aggressor side
    at the moment of execution. We post both sides as 1-level books and
    advance them on every print. `depth_per_side` controls the simulated
    resting size on each side (uniform).
    """
    df = catalog.read_trades(token_id, start, end)
    if df.empty:
        return []

    deltas_out: list[OrderBookDeltas] = []
    last_buy = None
    last_sell = None
    for row in df.sort_values("timestamp").itertuples(index=False):
        ts_ms = int(row.timestamp)
        ts_ns = ts_ms * 1_000_000
        price = max(0.01, min(0.99, round(float(row.price), 2)))
        side = (row.side or "").upper()

        if side == "BUY":
            last_buy = price
        elif side == "SELL":
            last_sell = price

        # Need at least one side to post anything.
        bid_price = last_sell if last_sell is not None else max(0.01, price - 0.01)
        ask_price = last_buy if last_buy is not None else min(0.99, price + 0.01)
        # Ensure a valid spread (bid < ask). If crossed, widen artificially.
        if bid_price >= ask_price:
            mid = (bid_price + ask_price) / 2.0
            bid_price = max(0.01, round(mid - 0.005, 2))
            ask_price = min(0.99, round(mid + 0.005, 2))
            if bid_price >= ask_price:
                ask_price = min(0.99, bid_price + 0.01)

        deltas = [
            OrderBookDelta.clear(instrument.id, 0, ts_ns, ts_ns),
            OrderBookDelta(
                instrument.id,
                BookAction.ADD,
                BookOrder(
                    OrderSide.BUY,
                    instrument.make_price(bid_price),
                    instrument.make_qty(depth_per_side),
                    0,
                ),
                0,
                0,
                ts_ns,
                ts_ns,
            ),
            OrderBookDelta(
                instrument.id,
                BookAction.ADD,
                BookOrder(
                    OrderSide.SELL,
                    instrument.make_price(ask_price),
                    instrument.make_qty(depth_per_side),
                    0,
                ),
                0,
                0,
                ts_ns,
                ts_ns,
            ),
        ]
        deltas_out.append(
            OrderBookDeltas(instrument_id=instrument.id, deltas=deltas)
        )
    return deltas_out
