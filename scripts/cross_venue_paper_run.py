#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from trading_lab.config import load_config
from trading_lab.research.cross_venue import load_cross_venue_spec, validate_cross_venue_spec
from trading_lab.runner.cross_venue_paper import (
    CrossVenueObserveRunner,
    build_cross_venue_observe_plan,
)


DEFAULT_HYPOTHESES_DIR = Path("research/hypotheses")


def resolve_cross_venue_hypothesis_path(slug: str, *, hypotheses_dir: Path = DEFAULT_HYPOTHESES_DIR) -> Path:
    candidates = [
        hypotheses_dir / slug / "spec.md",
        hypotheses_dir / slug / "dossier.md",
        hypotheses_dir / f"{slug}.md",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"cross-venue hypothesis not found for slug={slug}")



def build_observe_run_plan(spec_path: str | Path, *, config=None, duration_secs: int = 300):
    cfg = config or load_config()
    spec = load_cross_venue_spec(spec_path)
    errors = validate_cross_venue_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    return build_cross_venue_observe_plan(config=cfg, spec=spec, duration_secs=duration_secs)



def build_dry_run_report(spec_path: str | Path, *, config=None, duration_secs: int = 300) -> dict:
    plan = build_observe_run_plan(spec_path, config=config, duration_secs=duration_secs)
    report = {
        "ok": True,
        "mode": "dry_run",
        "slug": plan.slug,
        "duration_secs": plan.duration_secs,
        "instrument_count": plan.instrument_count,
        "spec_path": str(spec_path),
        "subscriptions": {
            "polymarket": list(plan.polymarket_token_ids),
            "hyperliquid": [plan.hyperliquid_symbol] if plan.hyperliquid_symbol else [],
        },
        "node_clients": {
            "data": sorted(plan.node_config.data_clients.keys()),
            "exec": sorted(plan.node_config.exec_clients.keys()),
        },
        "instrument_ids": list(plan.instrument_ids),
    }
    return report



def build_live_run_report(spec_path: str | Path, *, config=None, duration_secs: int = 300, node=None) -> dict:
    cfg = config or load_config()
    spec = load_cross_venue_spec(spec_path)
    errors = validate_cross_venue_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    summary = CrossVenueObserveRunner(config=cfg, spec=spec, duration_secs=duration_secs).run(node=node)
    report = summary.to_dict()
    report.update({
        "ok": True,
        "mode": "run",
        "spec_path": str(spec_path),
    })
    return report



def main() -> int:
    p = argparse.ArgumentParser(description="Prepare or run an observe-only cross-venue paper session.")
    p.add_argument("--slug", help="Cross-venue hypothesis slug under research/hypotheses/")
    p.add_argument("--file", type=Path, help="Explicit cross-venue spec/dossier path")
    p.add_argument("--duration-secs", type=int, default=300)
    p.add_argument("--start", action="store_true", help="Run the bounded observe-only TradingNode session instead of dry-run planning")
    args = p.parse_args()

    if not args.slug and not args.file:
        print(json.dumps({"ok": False, "error": "missing_slug_or_file"}))
        return 2

    try:
        spec_path = args.file or resolve_cross_venue_hypothesis_path(args.slug)
        if args.start:
            report = build_live_run_report(spec_path, duration_secs=args.duration_secs)
        else:
            report = build_dry_run_report(spec_path, duration_secs=args.duration_secs)
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "error": "hypothesis_not_found", "detail": str(exc)}))
        return 2
    except NotImplementedError as exc:
        print(json.dumps({"ok": False, "error": "unsupported_runtime", "detail": str(exc)}))
        return 2
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "invalid_cross_venue_spec", "detail": str(exc)}))
        return 2

    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
