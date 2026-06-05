from __future__ import annotations

from trading_lab.agent.lifecycle import State
from trading_lab.research.eval_methodology import assess_backtest


def test_assess_backtest_rejects_insufficient_trades() -> None:
    got = assess_backtest(
        state_enum=State,
        sharpe=2.0,
        max_dd_pct=-1.0,
        n_trades=5,
        pnl_usdc=25.0,
        expectancy_usdc=5.0,
        fill_rate=0.5,
        n_orders=10,
        n_fills=5,
    )
    assert got.new_state == State.REJECTED.value
    assert got.rejection_category == "insufficient_trades"


def test_assess_backtest_shelves_thin_execution() -> None:
    got = assess_backtest(
        state_enum=State,
        sharpe=1.2,
        max_dd_pct=-3.0,
        n_trades=40,
        pnl_usdc=20.0,
        expectancy_usdc=0.5,
        fill_rate=0.02,
        n_orders=500,
        n_fills=10,
        min_fill_rate=0.05,
    )
    assert got.new_state == State.SHELVED.value
    assert got.rejection_category == "thin_execution"
    assert "thin_execution" in got.methodology["sample_quality"]["warnings"]


def test_assess_backtest_shelves_insufficient_breadth() -> None:
    got = assess_backtest(
        state_enum=State,
        sharpe=1.2,
        max_dd_pct=-3.0,
        n_trades=40,
        pnl_usdc=20.0,
        expectancy_usdc=0.5,
        fill_rate=0.4,
        n_orders=100,
        n_fills=40,
        n_markets=5,
        n_markets_with_fills=1,
        min_markets_with_fills=2,
    )
    assert got.new_state == State.SHELVED.value
    assert got.rejection_category == "insufficient_breadth"


def test_assess_backtest_positive_pnl_negative_sharpe_can_optimize() -> None:
    got = assess_backtest(
        state_enum=State,
        sharpe=-0.2,
        max_dd_pct=-5.0,
        n_trades=120,
        pnl_usdc=50.0,
        expectancy_usdc=50.0 / 120.0,
        fill_rate=0.4,
        n_orders=300,
        n_fills=120,
    )
    assert got.new_state == State.OPTIMIZE.value
    assert got.rejection_category == ""
