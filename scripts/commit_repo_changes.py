#!/usr/bin/env python3
"""Stage and commit path-scoped repo changes for scheduled jobs.

Designed for cron use in the Trading-Lab repo:
- only touches explicitly listed paths
- can force-add ignored outputs when the operator wants generated artifacts in git
- stays silent / exits 0 on no-op
- prints one JSON summary for auditability
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _split_paths(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paths", required=True, help="Comma-separated repo-relative paths to stage.")
    p.add_argument("--message", required=True, help="Commit message.")
    p.add_argument(
        "--force",
        action="store_true",
        help="Use git add -f so ignored generated outputs can be committed.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo = Path.cwd()
    paths = _split_paths(args.paths)
    if not paths:
        print(json.dumps({"ok": False, "error": "no_paths"}))
        return 2

    add_args = ["add"]
    if args.force:
        add_args.append("-f")
    add_args.extend(["-A", "--", *paths])
    add = _run_git(add_args, cwd=repo)
    if add.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "git_add_failed",
                    "stderr": add.stderr.strip(),
                    "paths": paths,
                }
            )
        )
        return 1

    staged = _run_git(["diff", "--cached", "--name-only", "--", *paths], cwd=repo)
    staged_files = [line.strip() for line in staged.stdout.splitlines() if line.strip()]
    if not staged_files:
        print(json.dumps({"ok": True, "status": "noop", "paths": paths}))
        return 0

    commit = _run_git(["commit", "-m", args.message], cwd=repo)
    if commit.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "git_commit_failed",
                    "stdout": commit.stdout.strip(),
                    "stderr": commit.stderr.strip(),
                    "paths": paths,
                    "staged_files": staged_files,
                }
            )
        )
        return 1

    rev = _run_git(["rev-parse", "HEAD"], cwd=repo)
    print(
        json.dumps(
            {
                "ok": True,
                "status": "committed",
                "commit": rev.stdout.strip(),
                "paths": paths,
                "files": staged_files,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
