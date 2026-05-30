"""Tests for funding PnL computation in `research.funding`."""

from __future__ import annotations

import pandas as pd

from trading_lab.research.funding import (
    compute_funding_pnl,
    equity_with_funding,
)


def test_no_position_no_funding_pnl():
    funding = pd.DataFrame({"ts_ms": [1000, 2000], "funding_rate": [0.0001, 0.0001]})
    pos = pd.DataFrame({"ts_ms": [], "qty": [], "coin": []}).astype({"ts_ms": "int64", "qty": "float64"})
    mark = pd.DataFrame({"ts_ms": [500, 1500], "mark_px": [100.0, 100.0]})
    res = compute_funding_pnl(pos, funding, mark)
    assert res.funding_pnl == 0.0
    assert res.n_funding_events == 0


def test_long_position_positive_funding_negative_pnl():
    """Long pays when rate > 0 → funding_pnl negative."""
    funding = pd.DataFrame({"ts_ms": [1500, 2500], "funding_rate": [0.001, 0.001]})
    # Position opened at 1000 with qty=2; held through both funding stamps.
    pos = pd.DataFrame({"ts_ms": [1000], "qty": [2.0], "coin": ["BTC"]})
    mark = pd.DataFrame({"ts_ms": [1400, 2400], "mark_px": [100.0, 100.0]})
    res = compute_funding_pnl(pos, funding, mark)
    # 2 events × -(2 * 100 * 0.001) = -0.4
    assert res.n_funding_events == 2
    assert res.funding_pnl < 0
    assert abs(res.funding_pnl - (-0.4)) < 1e-9


def test_short_position_positive_funding_positive_pnl():
    funding = pd.DataFrame({"ts_ms": [1500], "funding_rate": [0.001]})
    pos = pd.DataFrame({"ts_ms": [1000], "qty": [-2.0], "coin": ["BTC"]})
    mark = pd.DataFrame({"ts_ms": [1400], "mark_px": [100.0]})
    res = compute_funding_pnl(pos, funding, mark)
    assert res.funding_pnl > 0


def test_funding_only_after_open_counts():
    """Funding events before any position is opened are skipped (qty=0 there)."""
    funding = pd.DataFrame({"ts_ms": [500, 1500], "funding_rate": [0.001, 0.001]})
    pos = pd.DataFrame({"ts_ms": [1000], "qty": [1.0], "coin": ["BTC"]})
    mark = pd.DataFrame({"ts_ms": [400, 1400], "mark_px": [100.0, 100.0]})
    res = compute_funding_pnl(pos, funding, mark)
    assert res.n_funding_events == 1   # only the 1500 stamp held a position


def test_equity_with_funding_adds_cumulative():
    idx = pd.date_range("2025-01-01", periods=5, freq="h", tz="UTC")
    base = pd.Series([10_000, 10_010, 10_020, 10_005, 10_015], index=idx)
    detail = pd.DataFrame(
        {
            "ts_ms": [
                int(idx[1].value // 1_000_000),
                int(idx[3].value // 1_000_000),
            ],
            "qty": [1.0, 1.0],
            "mark_px": [100.0, 100.0],
            "funding_rate": [0.001, 0.001],
            "contribution": [-0.1, -0.1],
        }
    )
    out = equity_with_funding(base, detail)
    # Equity at idx[0] unchanged; later points reduced by cumulative funding.
    assert out.iloc[0] == 10_000
    assert out.iloc[-1] == 10_015 - 0.2
