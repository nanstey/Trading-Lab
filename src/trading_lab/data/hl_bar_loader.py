"""
Hyperliquid bar loader.

Reads Parquet candles from `HyperliquidCatalog` and emits NautilusTrader
`Bar` objects ready to be fed into a `BacktestEngine` via `add_data(bars,
sort=True)`.

We expose helpers to:
  * pick the right `BarType` for a (coin, interval) — `make_bar_type()`
  * build the perpetual instrument descriptor — re-exported from
    `venues.hyperliquid.instruments` for one-stop import
  * convert a candle DataFrame into a list of `Bar` events with NT-correct
    `Price`/`Quantity` precision and nanosecond timestamps
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
from nautilus_trader.model.instruments.crypto_perpetual import CryptoPerpetual
from nautilus_trader.model.objects import Price, Quantity

from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.venues.hyperliquid.instruments import (
    HYPERLIQUID_VENUE,
    hl_instrument_id,
    make_hl_perpetual,
)

# Map our string intervals to NT BarSpecification (step, aggregation).
_INTERVAL_SPEC: dict[str, tuple[int, BarAggregation]] = {
    "1m": (1, BarAggregation.MINUTE),
    "5m": (5, BarAggregation.MINUTE),
    "15m": (15, BarAggregation.MINUTE),
    "1h": (1, BarAggregation.HOUR),
    "4h": (4, BarAggregation.HOUR),
    "1d": (1, BarAggregation.DAY),
}


def make_bar_type(coin: str, interval: str) -> BarType:
    if interval not in _INTERVAL_SPEC:
        raise ValueError(f"Unsupported interval {interval!r}; choose from {list(_INTERVAL_SPEC)}")
    step, agg = _INTERVAL_SPEC[interval]
    spec = BarSpecification(step, agg, PriceType.LAST)
    return BarType(
        instrument_id=hl_instrument_id(coin),
        bar_spec=spec,
        aggregation_source=AggregationSource.EXTERNAL,
    )


def make_hl_instrument(coin: str, price_precision: int = 2, size_precision: int = 6) -> CryptoPerpetual:
    """Convenience re-export. Existing factory lives in venues/hyperliquid/instruments."""
    return make_hl_perpetual(coin, price_precision=price_precision, size_precision=size_precision)


def load_bars(
    catalog: HyperliquidCatalog,
    coin: str,
    interval: str,
    start: datetime,
    end: datetime,
    *,
    instrument: CryptoPerpetual | None = None,
) -> tuple[list[Bar], CryptoPerpetual]:
    """
    Load NT Bar events for `coin` at `interval` over `[start, end]`.

    Returns `(bars, instrument)`. Instrument is created if not supplied so
    callers can simply pass it through to `engine.add_instrument(...)`.
    """
    df = catalog.read_candles(coin, interval, start, end)
    if instrument is None:
        instrument = _infer_instrument(coin, df)
    bars = candles_to_bars(df, coin, interval, instrument)
    return bars, instrument


def candles_to_bars(
    df: pd.DataFrame,
    coin: str,
    interval: str,
    instrument: CryptoPerpetual,
) -> list[Bar]:
    if df.empty:
        return []
    bar_type = make_bar_type(coin, interval)
    pp = instrument.price_precision
    sp = instrument.size_precision
    bars: list[Bar] = []
    for row in df.itertuples(index=False):
        ts_init_ns = int(row.ts_close_ms) * 1_000_000
        ts_event_ns = int(row.ts_close_ms) * 1_000_000
        bars.append(
            Bar(
                bar_type,
                Price(float(row.open), pp),
                Price(float(row.high), pp),
                Price(float(row.low), pp),
                Price(float(row.close), pp),
                Quantity(float(row.volume), sp),
                ts_event_ns,
                ts_init_ns,
            )
        )
    return bars


def _infer_instrument(coin: str, df: pd.DataFrame) -> CryptoPerpetual:
    """
    Pick a `price_precision` that won't lose information for the price levels
    actually present in the data. HL meta exposes `szDecimals`; price precision
    is per-asset and depends on price magnitude (HL doc: "max 5 significant
    figures"). Empirically a fixed precision of 6 covers all coins.
    """
    return make_hl_perpetual(coin, price_precision=6, size_precision=6)


# Lightweight metadata struct for downstream summaries.
def bars_coverage(bars: list[Bar]) -> dict[str, Any]:
    if not bars:
        return {"count": 0}
    first = bars[0]
    last = bars[-1]
    return {
        "count": len(bars),
        "first_ts_ns": int(first.ts_init),
        "last_ts_ns": int(last.ts_init),
        "bar_type": str(first.bar_type),
    }


__all__ = [
    "HYPERLIQUID_VENUE",
    "candles_to_bars",
    "load_bars",
    "make_bar_type",
    "make_hl_instrument",
]
