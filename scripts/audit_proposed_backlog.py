#!/usr/bin/env python3
"""Audit lifecycle PROPOSED rows after the middle-pipeline migration.

For each row at state=PROPOSED:
  - if research/hypotheses/<slug>/spec.md exists and validates -> keep
  - else if strategy_module is set OR experiments exist -> DRIFT (leave;
    a human needs to decide whether to demote)
  - else -> source-summary; delete row + its transitions (the content
    survives as research/hypotheses/<slug>/dossier.md and an ingestion
    row at DOSSIER_READY/PENDING)

Dry-run by default. Pass --apply to delete.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent.spec_validation import validate_spec_markdown  # noqa: E402


def _experiments_count(conn: sqlite3.Connection, slug: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM experiments WHERE slug=?", (slug,)).fetchone()
    return int(row["n"])


def _delete(conn: sqlite3.Connection, slug: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM lifecycle_transitions WHERE slug=?", (slug,))
    conn.execute("DELETE FROM hypotheses WHERE slug=?", (slug,))
    conn.execute("COMMIT")


def audit(*, db_path: Path, hypotheses_dir: Path, apply: bool) -> dict:
    actions: list[dict] = []
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT slug, state, strategy_module FROM hypotheses WHERE state='PROPOSED' ORDER BY slug"
        ).fetchall()
        for r in rows:
            slug = r["slug"]
            spec = hypotheses_dir / slug / "spec.md"
            spec_ok = False
            if spec.exists():
                spec_ok = validate_spec_markdown(spec.read_text()).is_valid
            n_exp = _experiments_count(conn, slug)
            has_module = bool((r["strategy_module"] or "").strip())

            if spec_ok:
                decision = "KEEP_SPEC_OK"
            elif n_exp > 0 or has_module:
                decision = "DRIFT_LEAVE"
            else:
                decision = "DELETE_SOURCE_SUMMARY"

            actions.append({
                "slug": slug,
                "decision": decision,
                "has_spec": spec.exists(),
                "spec_valid": spec_ok,
                "n_experiments": n_exp,
                "has_strategy_module": has_module,
            })

            if apply and decision == "DELETE_SOURCE_SUMMARY":
                _delete(conn, slug)
    finally:
        conn.close()
    return {"applied": apply, "actions": actions}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--hypotheses-dir", type=Path, default=Path("research/hypotheses"))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = audit(db_path=args.db, hypotheses_dir=args.hypotheses_dir, apply=args.apply)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"== audit ({'APPLIED' if result['applied'] else 'DRY-RUN'}) ==")
        for a in result["actions"]:
            print(f"  {a['decision']:<26} {a['slug']}  (spec={a['has_spec']}/{a['spec_valid']}, exp={a['n_experiments']}, mod={a['has_strategy_module']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
