#!/usr/bin/env python3
"""
Clear the persistent kill-switch flag.

This is a deliberately friction-laden action: requires `--confirm` to run.
Don't reset the flag without understanding why it was tripped — read the
JSON in data/.kill_switch first.

Usage:
    python scripts/reset_kill_switch.py --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required — confirms you've read the flag and want to clear it.",
    )
    p.add_argument("--flag-path", type=Path, default=Path("data/.kill_switch"))
    args = p.parse_args()

    from nautilus_predict.risk.kill_switch import clear_flag, read_flag

    existing = read_flag(args.flag_path)
    if not existing:
        print(json.dumps({"cleared": False, "reason": "no flag present"}))
        return 0

    if not args.confirm:
        print(json.dumps({
            "cleared": False,
            "reason": "missing --confirm",
            "existing": existing,
        }))
        return 2

    cleared = clear_flag(args.flag_path)
    print(json.dumps({"cleared": cleared, "prior": existing}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
