"""
Performance metrics for backtests.

The brief calls out "don't rely on a single metric" — so this module computes
the full standard suite (Sharpe, Sortino, Calmar, profit factor, expectancy,
win/loss ratio, max drawdown) plus crypto-perp specific decompositions
(funding PnL vs price PnL, turnover, cost drag).

Two input shapes are supported:

  * `per_trade_returns`  : list of realised PnL per trade (USDC).
                           Use for trade-level Sharpe / win-loss / profit
                           factor / expectancy.
  * `equity_curve`       : pandas Series indexed by time, value = total equity.
                           Use for time-series Sharpe / Sortino / Calmar /
                           max DD. Bar-spaced (e.g. 5m or 1h) — pass the
                           correct `periods_per_year` for annualisation.

Everything returns plain Python floats so the result dict stays JSON-safe
for the experiments DB and downstream agents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

PERIODS_PER_YEAR_BY_INTERVAL: dict[str, float] = {
    "1m": 365 * 24 * 60,
    "5m": 365 * 24 * 12,
    "15m": 365 * 24 * 4,
    "1h": 365 * 24,
    "2h": 365 * 12,
    "3h": 365 * 8,
    "4h": 365 * 6,
    "1d": 365.0,
}


@dataclass
class PerformanceMetrics:
    """Comprehensive metrics bundle. All values are JSON-safe floats/ints."""

    # Trade-level
    n_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_loss_ratio: float = 0.0
    trade_sharpe: float = 0.0

    # Equity-curve (time-series) metrics
    total_return: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown_pct: float = 0.0
    annualised_volatility: float = 0.0

    # Crypto-perp decomposition
    price_pnl: float = 0.0
    funding_pnl: float = 0.0
    fees_paid: float = 0.0
    turnover_notional: float = 0.0

    # Diagnostics
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "extras"}
        out["extras"] = dict(self.extras)
        return out


def compute_trade_metrics(per_trade_pnl: list[float]) -> dict[str, float]:
    """Trade-level metrics. Handles empty input by returning all zeros."""
    n = len(per_trade_pnl)
    if n == 0:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "win_loss_ratio": 0.0,
            "trade_sharpe": 0.0,
        }

    arr = np.asarray(per_trade_pnl, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]

    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())  # positive number

    win_rate = float(len(wins) / n)
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0  # negative number
    expectancy = float(arr.mean())

    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else (
        math.inf if gross_profit > 0 else 0.0
    )
    win_loss_ratio = float(avg_win / -avg_loss) if avg_loss < 0 else (
        math.inf if avg_win > 0 else 0.0
    )

    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    trade_sharpe = float(expectancy / std * math.sqrt(n)) if std > 0 else 0.0

    return {
        "n_trades": int(n),
        "win_rate": win_rate,
        "profit_factor": _finite_or_zero(profit_factor),
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": _finite_or_zero(win_loss_ratio),
        "trade_sharpe": trade_sharpe,
    }


def compute_equity_metrics(
    equity_curve: pd.Series,
    *,
    periods_per_year: float,
    initial_capital: float | None = None,
) -> dict[str, float]:
    """
    Time-series metrics on a bar-spaced equity series.

    `equity_curve` index is ignored for math (we use position, not elapsed
    time, for annualisation — assumes evenly-spaced bars, which holds for
    HL candle data).
    """
    if equity_curve is None or len(equity_curve) < 2:
        return {
            "total_return": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "max_drawdown_pct": 0.0,
            "annualised_volatility": 0.0,
        }

    eq = pd.to_numeric(equity_curve, errors="coerce").dropna().astype(float)
    if len(eq) < 2:
        return {
            "total_return": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "max_drawdown_pct": 0.0,
            "annualised_volatility": 0.0,
        }

    start = float(initial_capital) if initial_capital is not None else float(eq.iloc[0])
    end = float(eq.iloc[-1])
    total_return = (end - start) / start if start != 0 else 0.0

    returns = eq.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if returns.empty:
        return {
            "total_return": float(total_return),
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "max_drawdown_pct": 0.0,
            "annualised_volatility": 0.0,
        }

    ann_factor = math.sqrt(periods_per_year)
    mean = float(returns.mean())
    std = float(returns.std(ddof=1))
    sharpe = (mean / std * ann_factor) if std > 0 else 0.0

    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mean / downside_std * ann_factor) if downside_std > 0 else 0.0

    running_max = eq.cummax()
    drawdown = (eq - running_max) / running_max
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0
    max_dd_pct = max_dd * 100.0

    # Calmar: annualised return / |max DD|.
    n_periods = len(eq) - 1
    years = n_periods / periods_per_year
    if years > 0 and start > 0 and end > 0:
        cagr = (end / start) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0
    calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0

    annualised_vol = std * ann_factor

    return {
        "total_return": float(total_return),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "max_drawdown_pct": float(max_dd_pct),
        "annualised_volatility": float(annualised_vol),
    }


def combine_metrics(
    per_trade_pnl: list[float],
    equity_curve: pd.Series,
    *,
    bar_interval: str,
    initial_capital: float | None = None,
    price_pnl: float = 0.0,
    funding_pnl: float = 0.0,
    fees_paid: float = 0.0,
    turnover_notional: float = 0.0,
    extras: dict[str, Any] | None = None,
) -> PerformanceMetrics:
    """One-shot wrapper that builds a full `PerformanceMetrics` bundle."""
    ppy = PERIODS_PER_YEAR_BY_INTERVAL.get(bar_interval, 365.0)
    trade = compute_trade_metrics(per_trade_pnl)
    equity = compute_equity_metrics(equity_curve, periods_per_year=ppy, initial_capital=initial_capital)
    pm = PerformanceMetrics(
        n_trades=int(trade["n_trades"]),
        win_rate=float(trade["win_rate"]),
        profit_factor=float(trade["profit_factor"]),
        expectancy=float(trade["expectancy"]),
        avg_win=float(trade["avg_win"]),
        avg_loss=float(trade["avg_loss"]),
        win_loss_ratio=float(trade["win_loss_ratio"]),
        trade_sharpe=float(trade["trade_sharpe"]),
        total_return=float(equity["total_return"]),
        sharpe=float(equity["sharpe"]),
        sortino=float(equity["sortino"]),
        calmar=float(equity["calmar"]),
        max_drawdown_pct=float(equity["max_drawdown_pct"]),
        annualised_volatility=float(equity["annualised_volatility"]),
        price_pnl=float(price_pnl),
        funding_pnl=float(funding_pnl),
        fees_paid=float(fees_paid),
        turnover_notional=float(turnover_notional),
        extras=dict(extras or {}),
    )
    return pm


def _finite_or_zero(x: float) -> float:
    if math.isnan(x) or math.isinf(x):
        return 0.0
    return x
