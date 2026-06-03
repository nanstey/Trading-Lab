from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hl_eval_strategy = _load_script_module("hl_eval_strategy", "scripts/hl_eval_strategy.py")


def test_extract_result_metrics_for_portfolio_payload() -> None:
    payload = {
        "mode": "portfolio",
        "result": {
            "per_market": [
                {"n_orders": 10, "n_fills": 8},
                {"n_orders": 5, "n_fills": 0},
            ],
            "portfolio_metrics": {
                "n_trades": 12,
                "sharpe": 1.25,
                "max_drawdown_pct": -4.5,
                "price_pnl": 90.0,
                "funding_pnl": 15.0,
                "extras": {"total_pnl": 105.0},
            },
            "n_markets": 2,
            "n_markets_with_fills": 1,
        },
    }
    got = hl_eval_strategy._extract_result_metrics(payload)
    assert got == {
        "sharpe": 1.25,
        "max_dd_pct": -4.5,
        "n_trades": 12,
        "pnl_usdc": 105.0,
        "fill_rate": 8 / 15,
        "n_orders": 15,
        "n_fills": 8,
        "n_markets": 2,
        "n_markets_with_fills": 1,
    }


def test_extract_result_metrics_falls_back_to_price_plus_funding() -> None:
    payload = {
        "mode": "single",
        "result": {
            "n_orders": 4,
            "n_fills": 2,
            "metrics": {
                "n_trades": 2,
                "sharpe": -0.5,
                "max_drawdown_pct": -1.0,
                "price_pnl": 10.0,
                "funding_pnl": 3.5,
                "extras": {},
            },
        },
    }
    got = hl_eval_strategy._extract_result_metrics(payload)
    assert got["pnl_usdc"] == 13.5
    assert got["fill_rate"] == 0.5
    assert got["n_markets"] == 1


def test_decide_positive_pnl_negative_sharpe_can_optimize() -> None:
    state, category = hl_eval_strategy.decide(
        sharpe=-0.2,
        max_dd_pct=-5.0,
        n_trades=120,
        pnl=50.0,
        min_trades=30,
    )
    assert state == "OPTIMIZE"
    assert category == ""
