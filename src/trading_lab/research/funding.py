"""
Funding PnL accounting for Hyperliquid perp backtests.

NT's `BacktestEngine` doesn't model perpetual funding natively. We compute
it as a post-processing step:

    funding_pnl = sum( -position_size * mark_px * funding_rate )

over every funding stamp where we held a position. Sign convention matches
the venue: positive funding rate means longs pay shorts, so a long position
takes a negative funding_pnl contribution. The amount is in quote currency
(USDC for HL).

Inputs:
  * `position_history` — pandas DataFrame with columns
    `["ts_ns", "coin", "qty"]`, where `qty` is signed (positive long,
    negative short) and rows are emitted whenever position changes.
  * `funding_history`  — pandas DataFrame as returned by
    `HyperliquidCatalog.read_funding(...)` (cols include `ts_ms`,
    `funding_rate`).
  * `mark_series`       — pandas DataFrame with columns `["ts_ms", "mark_px"]`,
    typically close-of-bar from 1h candles.

The position held at each funding stamp is the most recent `qty` from
`position_history` at or before `funding.ts_ms`. The mark price used is the
last close at or before the funding stamp.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class FundingResult:
    funding_pnl: float
    n_funding_events: int
    detail: pd.DataFrame  # ts, qty, mark_px, funding_rate, contribution


def compute_funding_pnl(
    position_history: pd.DataFrame,
    funding_history: pd.DataFrame,
    mark_series: pd.DataFrame,
) -> FundingResult:
    if funding_history is None or funding_history.empty:
        return FundingResult(0.0, 0, _empty_detail())
    if position_history is None or position_history.empty:
        return FundingResult(0.0, 0, _empty_detail())
    if mark_series is None or mark_series.empty:
        return FundingResult(0.0, 0, _empty_detail())

    fh = funding_history.sort_values("ts_ms").reset_index(drop=True).copy()
    ph = position_history.copy()
    if "ts_ms" not in ph.columns and "ts_ns" in ph.columns:
        ph["ts_ms"] = (ph["ts_ns"] // 1_000_000).astype("int64")
    ph = ph.sort_values("ts_ms").reset_index(drop=True)

    mk = mark_series.sort_values("ts_ms").reset_index(drop=True)

    # Use merge_asof to align — both must be sorted on the join key.
    aligned_qty = pd.merge_asof(
        fh[["ts_ms"]],
        ph[["ts_ms", "qty"]],
        on="ts_ms",
        direction="backward",
    )
    aligned_mark = pd.merge_asof(
        fh[["ts_ms"]],
        mk[["ts_ms", "mark_px"]],
        on="ts_ms",
        direction="backward",
    )
    fh["qty"] = aligned_qty["qty"].fillna(0.0).astype(float)
    fh["mark_px"] = aligned_mark["mark_px"].ffill().fillna(0.0).astype(float)
    fh["contribution"] = -fh["qty"] * fh["mark_px"] * fh["funding_rate"].astype(float)
    nonzero = fh[fh["qty"] != 0.0]
    return FundingResult(
        funding_pnl=float(nonzero["contribution"].sum()),
        n_funding_events=int(len(nonzero)),
        detail=nonzero[["ts_ms", "qty", "mark_px", "funding_rate", "contribution"]].reset_index(drop=True),
    )


def equity_with_funding(
    base_equity: pd.Series,
    funding_detail: pd.DataFrame,
) -> pd.Series:
    """
    Add cumulative funding contributions to a base equity curve.

    Both inputs must be on comparable time axes. `base_equity` is indexed by
    timestamp (datetime, UTC); funding contributions are stamped per event in
    `funding_detail["ts_ms"]`.
    """
    if funding_detail is None or funding_detail.empty:
        return base_equity.copy()
    series = funding_detail[["ts_ms", "contribution"]].copy()
    series["dt"] = pd.to_datetime(series["ts_ms"], unit="ms", utc=True)
    cum = series.set_index("dt")["contribution"].cumsum()
    # Reindex onto base_equity's index, forward-filling between funding events.
    cum = cum.reindex(base_equity.index, method="ffill").fillna(0.0)
    return (base_equity + cum).astype(float)


def _empty_detail() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["ts_ms", "qty", "mark_px", "funding_rate", "contribution"]
    ).astype({"ts_ms": "int64", "qty": "float64", "mark_px": "float64",
              "funding_rate": "float64", "contribution": "float64"})
