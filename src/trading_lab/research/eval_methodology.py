"""Shared backtest evaluation methodology.

Turns raw backtest metrics into:
- sample / execution quality diagnostics
- a state-transition recommendation
- a concise methodology block for operator-facing JSON

This is intentionally venue-agnostic so both Polymarket and Hyperliquid eval
paths can apply the same reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvalDecision:
    new_state: str
    rejection_category: str
    methodology: dict[str, Any]


def assess_backtest(
    *,
    state_enum,
    sharpe: float,
    max_dd_pct: float,
    n_trades: int,
    pnl_usdc: float,
    expectancy_usdc: float,
    fill_rate: float,
    n_orders: int,
    n_fills: int,
    n_markets: int = 1,
    n_markets_with_fills: int = 1,
    min_trades: int = 30,
    min_fill_rate: float = 0.05,
    min_markets_with_fills: int = 2,
) -> EvalDecision:
    warnings: list[str] = []
    gates: dict[str, bool] = {
        "enough_trades": n_trades >= min_trades,
        "has_fills": n_fills > 0,
        "positive_pnl": pnl_usdc > 0,
        "positive_expectancy": expectancy_usdc > 0,
        "acceptable_drawdown": abs(max_dd_pct) <= 20.0,
    }

    if n_orders > 0 and fill_rate < min_fill_rate:
        warnings.append("thin_execution")
    if n_markets > 1 and n_markets_with_fills < min_markets_with_fills:
        warnings.append("insufficient_breadth")
    if n_trades < max(min_trades * 3, 100):
        warnings.append("small_sample")
    if abs(max_dd_pct) > 20.0:
        warnings.append("high_drawdown")

    methodology = {
        "expectancy_usdc": float(expectancy_usdc),
        "fill_rate": float(fill_rate),
        "n_orders": int(n_orders),
        "n_fills": int(n_fills),
        "n_markets": int(n_markets),
        "n_markets_with_fills": int(n_markets_with_fills),
        "sample_quality": {
            "min_trades_required": int(min_trades),
            "min_fill_rate_required": float(min_fill_rate),
            "min_markets_with_fills_required": int(min_markets_with_fills if n_markets > 1 else 1),
            "warnings": warnings,
            "gates": gates,
        },
    }

    if n_trades < min_trades:
        return EvalDecision(state_enum.REJECTED.value, "insufficient_trades", methodology)
    if n_fills <= 0:
        return EvalDecision(state_enum.REJECTED.value, "no_fills", methodology)
    if n_markets > 1 and n_markets_with_fills < min_markets_with_fills:
        return EvalDecision(state_enum.SHELVED.value, "insufficient_breadth", methodology)
    if n_orders > 0 and fill_rate < min_fill_rate:
        return EvalDecision(state_enum.SHELVED.value, "thin_execution", methodology)
    if pnl_usdc < 0 or expectancy_usdc <= 0:
        return EvalDecision(state_enum.REJECTED.value, "unprofitable", methodology)
    if pnl_usdc > 0 and sharpe < 0 and n_trades >= max(100, min_trades * 3):
        return EvalDecision(state_enum.OPTIMIZE.value, "", methodology)
    if sharpe < 0.5:
        return EvalDecision(state_enum.SHELVED.value, "marginal_is", methodology)
    if sharpe < 1.0:
        if abs(max_dd_pct) > 25:
            return EvalDecision(state_enum.REJECTED.value, "high_dd", methodology)
        return EvalDecision(state_enum.SHELVED.value, "marginal_is", methodology)
    if abs(max_dd_pct) > 20:
        return EvalDecision(state_enum.REJECTED.value, "high_dd", methodology)
    return EvalDecision(state_enum.OPTIMIZE.value, "", methodology)
