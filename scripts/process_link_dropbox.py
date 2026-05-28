#!/usr/bin/env python3
"""Process manually dropped links into the Trading-Lab research inbox."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dropbox", type=Path, default=Path("research/link_dropbox"))
    p.add_argument("--inbox", type=Path, default=Path("research/manual_inbox"))
    p.add_argument("--captures-root", type=Path, default=Path("research/captures"))
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from trading_lab.agent import lifecycle, source_capture

    lifecycle.init_db(args.db)
    result = source_capture.process_link_dropbox(
        dropbox_dir=args.dropbox,
        inbox_dir=args.inbox,
        captures_root=args.captures_root,
        db_path=args.db,
        dry_run=args.dry_run,
    )
    print(json.dumps(result))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
