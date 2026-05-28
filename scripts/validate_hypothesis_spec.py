#!/usr/bin/env python3
"""Thin CLI wrapper around trading_lab.agent.spec_validation.

Validate one spec.md path, or every spec in research/hypotheses/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent.spec_validation import validate_spec_markdown  # noqa: E402


def _validate_one(path: Path) -> dict:
    text = path.read_text() if path.exists() else ""
    if not text:
        return {"path": str(path), "ok": False, "reason": "missing or empty"}
    result = validate_spec_markdown(text)
    return {
        "path": str(path),
        "ok": result.is_valid,
        "missing_sections": list(result.missing_sections),
        "empty_sections": list(result.empty_sections),
        "reason": result.reason,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--path", type=Path, help="One spec.md file to validate")
    grp.add_argument("--all", action="store_true", help="Validate every spec.md under --hypotheses-dir")
    p.add_argument("--hypotheses-dir", type=Path, default=Path("research/hypotheses"))
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.path:
        results = [_validate_one(args.path)]
    elif args.all:
        results = [
            _validate_one(spec)
            for spec in sorted(args.hypotheses_dir.glob("*/spec.md"))
        ]
    else:
        p.error("pass --path or --all")
        return 2

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            flag = "OK   " if r["ok"] else "FAIL "
            print(f"{flag} {r['path']}  {r['reason']}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
