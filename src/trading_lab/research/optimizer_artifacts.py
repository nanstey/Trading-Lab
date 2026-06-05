"""Shared optimizer artifact helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA_VERSION = "v1"
ARTIFACT_KIND = "optimizer_summary"


def default_output_path(*, slug: str, data_start: str, data_end: str, root: Path | None = None) -> Path:
    base = root or Path("research/optimizer_outputs")
    return base / f"{slug}_{data_start}_{data_end}.json"


def normalize_optimizer_summary(summary: dict[str, Any]) -> dict[str, Any]:
    out = dict(summary)
    candidate_ranking = list(out.get("candidate_ranking") or [])
    warnings = list(out.get("warnings") or [])
    decision_state = out.get("decision_new_state") or out.get("decision_state") or ""
    decision_category = out.get("decision_rejection_category") or out.get("decision_reason") or ""
    venue = str(out.get("venue") or "polymarket")
    slug = str(out.get("slug") or "")
    output_file = out.get("output_file")

    best_summary = {
        "params": out.get("best_params") or {},
        "methodology_score": out.get("best_methodology_score"),
        "methodology": out.get("best_methodology"),
        "methodology_state": out.get("best_methodology_state"),
        "methodology_category": out.get("best_methodology_category"),
        "oos_mean_sharpe": out.get("best_oos_mean_sharpe"),
        "oos_mean_pnl": out.get("best_oos_mean_pnl", out.get("best_oos_total_pnl")),
        "recent_oos_sharpe": out.get("best_recent_oos_sharpe"),
        "recent_oos_pnl": out.get("best_recent_oos_pnl", out.get("best_oos_total_pnl")),
        "min_oos_trades": out.get("best_oos_min_trades"),
        "is_sharpe": out.get("best_is_sharpe"),
        "config_idx": out.get("best_config_idx"),
    }

    if not candidate_ranking and best_summary["params"]:
        candidate_ranking = [
            {
                "params": best_summary["params"],
                "score": best_summary["methodology_score"],
                "state": best_summary["methodology_state"],
                "category": best_summary["methodology_category"],
                "sharpe": best_summary["oos_mean_sharpe"],
                "pnl": best_summary["oos_mean_pnl"],
                "n_trades": best_summary["min_oos_trades"],
            }
        ]

    normalized = {
        **out,
        "artifact_kind": ARTIFACT_KIND,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "slug": slug,
        "venue": venue,
        "decision_new_state": decision_state,
        "decision_rejection_category": decision_category,
        "candidate_ranking": candidate_ranking,
        "best_summary": best_summary,
        "warnings": warnings,
    }
    if output_file is not None:
        normalized["output_file"] = str(output_file)
    return normalized


def write_optimizer_artifact(summary: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    normalized = normalize_optimizer_summary(summary)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized["output_file"] = str(output_path)
    output_path.write_text(json.dumps(normalized, indent=2, default=str) + "\n")
    return normalized
