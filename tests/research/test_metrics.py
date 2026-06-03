"""Tests for `research.metrics`."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from trading_lab.research.metrics import (
    PERIODS_PER_YEAR_BY_INTERVAL,
    combine_metrics,
    compute_equity_metrics,
    compute_trade_metrics,
)


def test_trade_metrics_empty():
    out = compute_trade_metrics([])
    assert out["n_trades"] == 0
    assert out["profit_factor"] == 0.0


def test_trade_metrics_all_wins():
    out = compute_trade_metrics([10.0, 5.0, 2.0])
    assert out["n_trades"] == 3
    assert out["win_rate"] == 1.0
    # Profit factor is inf when there are no losses → coerced to 0 (per _finite_or_zero).
    assert out["profit_factor"] == 0.0


def test_trade_metrics_mixed():
    out = compute_trade_metrics([5.0, -2.0, 3.0, -1.0])
    assert out["n_trades"] == 4
    assert out["win_rate"] == 0.5
    assert out["profit_factor"] == 8.0 / 3.0     # gross profit 8, gross loss 3
    assert math.isclose(out["expectancy"], 1.25)
    assert math.isclose(out["avg_win"], 4.0)
    assert math.isclose(out["avg_loss"], -1.5)


def test_equity_metrics_flat_returns_zero_sharpe():
    eq = pd.Series([10_000.0] * 100)
    res = compute_equity_metrics(eq, periods_per_year=365)
    assert res["sharpe"] == 0.0
    assert res["sortino"] == 0.0


def test_equity_metrics_monotonic_up_positive_sharpe():
    eq = pd.Series(10_000 + np.arange(100) * 10.0)
    res = compute_equity_metrics(eq, periods_per_year=365)
    assert res["sharpe"] > 0
    assert res["max_drawdown_pct"] == 0.0


def test_equity_metrics_drawdown_negative():
    # Equity goes up then crashes — should show negative max DD.
    eq = pd.Series(list(np.linspace(10_000, 12_000, 50)) + list(np.linspace(12_000, 9_000, 50)))
    res = compute_equity_metrics(eq, periods_per_year=365)
    assert res["max_drawdown_pct"] < 0.0
    assert res["max_drawdown_pct"] > -50  # %



def test_equity_metrics_negative_terminal_equity_keeps_calmar_real() -> None:
    eq = pd.Series([10_000.0, 5_000.0, -100.0])
    res = compute_equity_metrics(eq, periods_per_year=365, initial_capital=10_000.0)
    assert isinstance(res["calmar"], float)
    assert res["calmar"] == 0.0


def test_combine_metrics_carries_through():
    eq = pd.Series(10_000 + np.arange(50) * 10.0)
    pm = combine_metrics(
        per_trade_pnl=[5.0, -2.0, 3.0],
        equity_curve=eq,
        bar_interval="1h",
        initial_capital=10_000.0,
        price_pnl=50.0,
        funding_pnl=1.5,
        fees_paid=2.0,
        turnover_notional=10_000.0,
    )
    assert pm.n_trades == 3
    assert pm.price_pnl == 50.0
    assert pm.funding_pnl == 1.5
    assert pm.fees_paid == 2.0
    assert pm.total_return > 0
    assert "1h" in PERIODS_PER_YEAR_BY_INTERVAL
