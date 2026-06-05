from __future__ import annotations

import json

from trading_lab.research.optimizer_artifacts import (
    ARTIFACT_KIND,
    ARTIFACT_SCHEMA_VERSION,
    normalize_optimizer_summary,
)


def test_normalize_optimizer_summary_preserves_generic_fields() -> None:
    raw = {
        "ok": True,
        "slug": "demo",
        "venue": "polymarket",
        "best_params": {"alpha": 1},
        "best_methodology_score": 123.0,
        "best_methodology": {"sample_quality": {"gates": {"positive_expectancy": True}}},
        "best_methodology_state": "PAPER_READY",
        "best_methodology_category": "",
        "best_oos_mean_sharpe": 1.2,
        "best_oos_mean_pnl": 25.0,
        "best_recent_oos_pnl": 12.0,
        "decision_new_state": "PAPER_READY",
        "decision_rejection_category": "",
        "candidate_ranking": [{"params": {"alpha": 1}, "score": 123.0}],
    }
    got = normalize_optimizer_summary(raw)
    assert got["artifact_kind"] == ARTIFACT_KIND
    assert got["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert got["best_summary"]["params"] == {"alpha": 1}
    assert got["candidate_ranking"][0]["score"] == 123.0
    assert got["decision_new_state"] == "PAPER_READY"


def test_normalize_optimizer_summary_bridges_hl_fields() -> None:
    raw = {
        "ok": True,
        "slug": "hl-demo",
        "venue": "hyperliquid",
        "best_config_idx": 2,
        "best_params": {"lookback": 20},
        "best_oos_mean_sharpe": 0.9,
        "best_oos_total_pnl": 44.0,
        "best_oos_min_trades": 15,
        "decision_state": "SHELVED",
        "decision_reason": "marginal_oos",
    }
    got = normalize_optimizer_summary(raw)
    assert got["artifact_kind"] == ARTIFACT_KIND
    assert got["best_summary"]["config_idx"] == 2
    assert got["best_summary"]["oos_mean_pnl"] == 44.0
    assert got["best_summary"]["recent_oos_pnl"] == 44.0
    assert got["decision_new_state"] == "SHELVED"
    assert got["decision_rejection_category"] == "marginal_oos"
    assert got["candidate_ranking"][0]["params"] == {"lookback": 20}
