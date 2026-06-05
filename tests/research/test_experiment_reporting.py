from __future__ import annotations

import json

from trading_lab.research.experiment_reporting import enrich_experiment_row, sort_experiments


def _row(**overrides):
    base = {
        "id": 1,
        "slug": "demo",
        "params_json": json.dumps({
            "alpha": 1,
            "_meta": {
                "expectancy_usdc": 0.4,
                "n_orders": 100,
                "n_fills": 40,
                "n_markets": 3,
                "n_markets_with_fills": 2,
            },
        }),
        "data_start": "2026-01-01",
        "data_end": "2026-02-01",
        "sharpe": 1.2,
        "max_dd": -5.0,
        "fill_rate": 0.4,
        "pnl": 20.0,
        "n_trades": 40,
        "walk_forward_oos_sharpe": None,
        "code_hash": "",
        "data_hash": "",
        "kill_switch_triggered": 0,
        "created_at": "2026-06-04T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_enrich_experiment_row_adds_methodology_fields() -> None:
    got = enrich_experiment_row(_row())
    assert got["expectancy_usdc"] == 0.4
    assert got["n_orders"] == 100
    assert got["n_markets_with_fills"] == 2
    assert got["methodology_decision_state"] == "OPTIMIZE"
    assert got["methodology_sort_score"] > 0


def test_sort_experiments_prefers_methodology_score() -> None:
    good = _row(id=1, pnl=10.0)
    bad = _row(
        id=2,
        pnl=50.0,
        fill_rate=0.01,
        params_json=json.dumps({
            "alpha": 2,
            "_meta": {
                "expectancy_usdc": 0.1,
                "n_orders": 500,
                "n_fills": 5,
                "n_markets": 5,
                "n_markets_with_fills": 1,
            },
        }),
    )
    rows = sort_experiments([bad, good], "score")
    assert rows[0]["id"] == 1
    assert rows[1]["methodology_decision_category"] in {"thin_execution", "insufficient_breadth"}


def test_sort_experiments_can_sort_by_expectancy() -> None:
    a = _row(id=1)
    b = _row(
        id=2,
        params_json=json.dumps({
            "alpha": 2,
            "_meta": {
                "expectancy_usdc": 0.8,
                "n_orders": 100,
                "n_fills": 40,
                "n_markets": 3,
                "n_markets_with_fills": 2,
            },
        }),
    )
    rows = sort_experiments([a, b], "expectancy")
    assert rows[0]["id"] == 2
