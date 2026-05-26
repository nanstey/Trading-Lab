#!/usr/bin/env python3
"""
Halt all trading by writing the persistent kill-switch flag.

After this runs, any new `KillSwitch(...)` instantiation will refuse to start
and raise `KillSwitchTriggered`. Running paper/live processes that load the
flag at startup will refuse to restart; running processes detect the flag on
their next reload.

Usage:
    python scripts/halt_trading.py --reason "manual halt — investigating fills"

Prints a JSON line on success (matches Phase 5.1 convention).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reason", required=True, help="Why are we halting?")
    p.add_argument(
        "--actor",
        default=os.environ.get("USER", "unknown"),
        help="Who triggered the halt? (default: $USER)",
    )
    p.add_argument(
        "--flag-path",
        type=Path,
        default=Path("data/.kill_switch"),
    )
    args = p.parse_args()

    from trading_lab.risk.kill_switch import write_flag

    write_flag(reason=args.reason, actor=args.actor, path=args.flag_path)
    print(json.dumps({
        "halted": True,
        "flag": str(args.flag_path),
        "reason": args.reason,
        "actor": args.actor,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
