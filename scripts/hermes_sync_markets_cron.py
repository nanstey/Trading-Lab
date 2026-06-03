#!/usr/bin/env python3
"""Hermes cron wrapper for Trading-Lab market metadata sync.

Purpose:
- keep the cron job deterministic and cheap (`no_agent=True` friendly)
- retry once on known transient Gamma pagination / HTTP 422 failures
- emit one short operator-facing status line
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data/market_catalog.db"
TRANSIENT_MARKERS = (
    "422",
    "http 422",
    "gamma",
    "pagination",
    "unprocessable",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--full", action="store_true", help="Run the full sync instead of active-only")
    p.add_argument("--page-size", type=int, default=50, help="Fallback page size for retry path")
    return p.parse_args()


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )


def _looks_transient(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in TRANSIENT_MARKERS)


def _detail(text: str) -> str:
    for line in reversed([line.strip() for line in text.splitlines()]):
        if line:
            return line[:160]
    return "no_detail"


def main() -> int:
    args = parse_args()
    label = "full" if args.full else "daily"
    primary = [
        ".venv/bin/python3",
        "scripts/sync_market_metadata.py",
        "--full" if args.full else "--active-only",
        "--page-size",
        str(args.page_size),
    ]
    fallback = [
        ".venv/bin/python3",
        "scripts/sync_market_metadata.py",
        "--full" if args.full else "--active-only",
        "--page-size",
        str(max(10, args.page_size // 2)),
    ]

    first = _run(primary)
    if first.returncode == 0 and DB_PATH.exists():
        print(f"sync-markets {label}: ok")
        return 0

    combined_first = f"{first.stdout}\n{first.stderr}"
    if not _looks_transient(combined_first):
        print(f"sync-markets {label}: failed ({_detail(combined_first)})")
        return 1

    second = _run(fallback)
    if second.returncode == 0 and DB_PATH.exists():
        print(f"sync-markets {label}: recovered after retry")
        return 0

    combined_second = f"{second.stdout}\n{second.stderr}"
    print(f"sync-markets {label}: failed ({_detail(combined_second)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
