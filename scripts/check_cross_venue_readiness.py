#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.research.cross_venue import load_cross_venue_spec, validate_cross_venue_spec


def build_readiness_report(path: str | Path) -> dict:
    spec = load_cross_venue_spec(path)
    errors = validate_cross_venue_spec(spec)

    gaps: list[str] = []
    backtest_ready = spec.hyperliquid.kind == "perp" and not errors
    if spec.hyperliquid.kind == "perp":
        gaps.extend(
            [
                "dual_venue_paper_runner_missing",
                "cross_venue_fair_value_model_missing",
                "cross_venue_legging_risk_state_machine_missing",
            ]
        )
    else:
        gaps.extend(
            [
                "hyperliquid_outcome_runtime_not_integrated",
                "dual_venue_backtest_runner_missing",
                "dual_venue_paper_runner_missing",
                "cross_venue_legging_risk_state_machine_missing",
            ]
        )

    return {
        "ok": not errors,
        "slug": spec.slug,
        "errors": errors,
        "spec": spec.to_dict(),
        "readiness": {
            "develop": not errors,
            "backtest": backtest_ready,
            "paper_trade": False,
            "live_trade": False,
        },
        "gaps": gaps,
        "notes": [
            "Perp cross-venue hypotheses now have synchronized timeline/backtest diagnostics.",
            "Cross-venue hypotheses still need execution/risk logic before paper trading.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check cross-venue HL/PM readiness for a hypothesis file.")
    parser.add_argument("--file", required=True, help="Path to cross-venue hypothesis markdown file")
    args = parser.parse_args()
    report = build_readiness_report(Path(args.file))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
