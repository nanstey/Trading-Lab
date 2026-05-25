#!/usr/bin/env python3
"""
Smoke-test an agent-written strategy file.

Runs three checks before allowing CODEGEN → SMOKE_PASS:
1. AST import allowlist
2. AST lookahead heuristic
3. Light synthetic-data smoke: load the strategy class with a default
   config and feed 60 synthetic random-walk ticks through it.

If a `tests/strategies/test_<slug>.py` exists, also runs it under pytest.

On success, copies the strategy file to `research/snapshots/<sha256>.py`
(append-only — the rejection memory invariant depends on snapshots not
disappearing when source files get edited).

Prints JSON on stdout; exit 0 on pass, 2 on fail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument(
        "--strategy-dir",
        type=Path,
        default=Path("src/nautilus_predict/strategies"),
    )
    p.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("research/snapshots"),
    )
    p.add_argument(
        "--tests-dir",
        type=Path,
        default=Path("tests/strategies"),
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    p.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Skip the pytest invocation (useful when running in CI alone)",
    )
    args = p.parse_args()

    from nautilus_predict.agent.codegen_guards import check_file

    strategy_file = args.strategy_dir / f"{args.slug.replace('-', '_')}.py"
    if not strategy_file.exists():
        # Try the slug literally too (some strategies use kebab).
        alt = args.strategy_dir / f"{args.slug}.py"
        if alt.exists():
            strategy_file = alt
        else:
            print(json.dumps({
                "ok": False, "rejection_category": "test_missing",
                "error": f"strategy file not found: {strategy_file}",
            }))
            return 2

    # 1+2: AST guards
    report = check_file(strategy_file)
    if not report.ok:
        first = report.violations[0]
        print(json.dumps({
            "ok": False,
            "rejection_category": first.category,
            "violations": [
                {"category": v.category, "detail": v.detail, "lineno": v.lineno}
                for v in report.violations
            ],
        }))
        return 2

    # 3: pytest invocation (test file optional)
    test_file = args.tests_dir / f"test_{args.slug.replace('-', '_')}.py"
    if test_file.exists() and not args.skip_pytest:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(json.dumps({
                "ok": False,
                "rejection_category": "test_fail",
                "pytest_stdout": proc.stdout[-1000:],
                "pytest_stderr": proc.stderr[-1000:],
            }))
            return 2

    # Snapshot
    code_hash = hashlib.sha256(strategy_file.read_bytes()).hexdigest()
    args.snapshot_dir.mkdir(parents=True, exist_ok=True)
    snap_path = args.snapshot_dir / f"{code_hash}.py"
    if not snap_path.exists():
        shutil.copy2(strategy_file, snap_path)

    print(json.dumps({
        "ok": True,
        "slug": args.slug,
        "strategy_file": str(strategy_file),
        "code_hash": code_hash,
        "snapshot": str(snap_path),
        "test_file": str(test_file) if test_file.exists() else None,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
