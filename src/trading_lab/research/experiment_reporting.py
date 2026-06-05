"""Experiment/result enrichment and ranking helpers for Trading-Lab."""

from __future__ import annotations

import json
from typing import Any

from trading_lab.agent.lifecycle import State
from trading_lab.research.eval_methodology import assess_backtest


_DECISION_SCORE = {
    State.PAPER_READY.value: 4,
    State.OPTIMIZE.value: 3,
    State.SHELVED.value: 2,
    State.REJECTED.value: 1,
}


def enrich_experiment_row(
    row: dict[str, Any],
    *,
    min_trades: int = 30,
    min_fill_rate: float = 0.05,
    min_markets_with_fills: int = 2,
) -> dict[str, Any]:
    out = dict(row)
    params = _parse_params_json(row.get("params_json"))
    meta = params.get("_meta", {}) if isinstance(params, dict) else {}

    expectancy_usdc = float(meta.get("expectancy_usdc", 0.0))
    n_orders = int(meta.get("n_orders", 0))
    n_fills = int(meta.get("n_fills", 0))
    n_markets = int(meta.get("n_markets", 1))
    n_markets_with_fills = int(meta.get("n_markets_with_fills", 1 if n_fills > 0 else 0))

    decision = assess_backtest(
        state_enum=State,
        sharpe=float(row.get("sharpe", 0.0) or 0.0),
        max_dd_pct=float(row.get("max_dd", 0.0) or 0.0),
        n_trades=int(row.get("n_trades", 0) or 0),
        pnl_usdc=float(row.get("pnl", 0.0) or 0.0),
        expectancy_usdc=expectancy_usdc,
        fill_rate=float(row.get("fill_rate", 0.0) or 0.0),
        n_orders=n_orders,
        n_fills=n_fills,
        n_markets=n_markets,
        n_markets_with_fills=n_markets_with_fills,
        min_trades=min_trades,
        min_fill_rate=min_fill_rate,
        min_markets_with_fills=min_markets_with_fills,
    )

    out["params"] = params
    out["methodology"] = decision.methodology
    out["methodology_decision_state"] = decision.new_state
    out["methodology_decision_category"] = decision.rejection_category
    out["expectancy_usdc"] = expectancy_usdc
    out["n_orders"] = n_orders
    out["n_fills"] = n_fills
    out["n_markets"] = n_markets
    out["n_markets_with_fills"] = n_markets_with_fills
    out["methodology_sort_score"] = float(score_backtest_result(out))
    return out


def score_backtest_result(result: dict[str, Any]) -> float:
    methodology = result.get("methodology") or {}
    sample = methodology.get("sample_quality") or {}
    gates = sample.get("gates") or {}
    state = result.get("methodology_decision_state", State.REJECTED.value)
    decision_score = _DECISION_SCORE.get(str(state), 0)

    enough_trades = 1.0 if gates.get("enough_trades") else 0.0
    has_fills = 1.0 if gates.get("has_fills") else 0.0
    positive_pnl = 1.0 if gates.get("positive_pnl") else 0.0
    positive_expectancy = 1.0 if gates.get("positive_expectancy") else 0.0
    acceptable_drawdown = 1.0 if gates.get("acceptable_drawdown") else 0.0

    fill_rate = float(result.get("fill_rate", 0.0) or 0.0)
    expectancy = float(result.get("expectancy_usdc", 0.0) or 0.0)
    pnl = float(result.get("pnl", result.get("pnl_usdc", 0.0)) or 0.0)
    sharpe = float(result.get("sharpe", 0.0) or 0.0)
    max_dd_penalty = abs(float(result.get("max_dd", result.get("max_dd_pct", 0.0)) or 0.0))
    breadth = float(result.get("n_markets_with_fills", 0) or 0)

    score = 0.0
    score += decision_score * 1_000_000.0
    score += enough_trades * 100_000.0
    score += has_fills * 100_000.0
    score += positive_pnl * 100_000.0
    score += positive_expectancy * 100_000.0
    score += acceptable_drawdown * 50_000.0
    score += breadth * 1_000.0
    score += fill_rate * 10_000.0
    score += expectancy * 1_000.0
    score += pnl * 10.0
    score += sharpe * 100.0
    score -= max_dd_penalty * 10.0
    return score


def sort_experiments(rows: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    enriched = [enrich_experiment_row(r) for r in rows]
    if sort_by == "created_at":
        return sorted(enriched, key=lambda r: (str(r.get("created_at", "")), int(r.get("id", 0))), reverse=True)
    if sort_by == "pnl":
        return sorted(enriched, key=lambda r: float(r.get("pnl", 0.0) or 0.0), reverse=True)
    if sort_by == "sharpe":
        return sorted(enriched, key=lambda r: float(r.get("sharpe", 0.0) or 0.0), reverse=True)
    if sort_by == "expectancy":
        return sorted(enriched, key=lambda r: float(r.get("expectancy_usdc", 0.0) or 0.0), reverse=True)
    if sort_by == "fill_rate":
        return sorted(enriched, key=lambda r: float(r.get("fill_rate", 0.0) or 0.0), reverse=True)
    if sort_by == "score":
        return sorted(enriched, key=lambda r: float(r.get("methodology_sort_score", 0.0) or 0.0), reverse=True)
    return enriched


def _parse_params_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
