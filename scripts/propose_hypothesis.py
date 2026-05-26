#!/usr/bin/env python3
"""
Register a hypothesis MD into the experiment DB.

Reads `research/hypotheses/<slug>.md` (frontmatter + body), parses the YAML
frontmatter, and inserts a row in PROPOSED state. Idempotent — re-running
with the same slug refreshes metadata but does NOT reset state.

Usage:
    python scripts/propose_hypothesis.py --file research/hypotheses/arb-complement.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def parse_md(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm = text[3:end].strip()
    body = text[end + 4 :].strip()
    try:
        import yaml
        data = yaml.safe_load(fm) or {}
    except Exception:
        data = {}
    return data, body


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", type=Path, required=True)
    p.add_argument(
        "--actor",
        default=f"user:{os.environ.get('USER', 'unknown')}",
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    p.add_argument("--initial-state", default=None,
                   help="Override state from frontmatter (e.g., BACKTEST)")
    args = p.parse_args()

    from trading_lab.agent import lifecycle

    fm, body = parse_md(args.file)
    if not fm:
        print(json.dumps({"ok": False, "error": "no frontmatter"}))
        return 2
    slug = fm.get("slug")
    if not slug:
        print(json.dumps({"ok": False, "error": "frontmatter missing slug"}))
        return 2

    state = args.initial_state or fm.get("state") or lifecycle.State.PROPOSED.value
    summary = body.split("\n\n", 1)[0][:500]  # first paragraph as quick summary

    h = lifecycle.add_hypothesis(
        slug=slug,
        state=state,
        source_url=fm.get("source_url") or "",
        source_type=fm.get("source") or "manual",
        summary=summary,
        parent_slug=fm.get("parent_slug"),
        market_criteria=fm.get("market_criteria") or {},
        strategy_module=fm.get("strategy_module") or "",
        strategy_class=fm.get("strategy_class") or "",
        strategy_config_class=fm.get("strategy_config_class") or "",
        actor=args.actor,
        db_path=args.db,
    )
    print(json.dumps({
        "ok": True,
        "slug": h.slug,
        "state": h.state,
        "registered_at": datetime.now(tz=UTC).isoformat(),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
