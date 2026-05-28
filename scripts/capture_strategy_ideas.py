#!/usr/bin/env python3
"""Capture external strategy ideas into `research/manual_inbox/`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sources", type=Path, default=Path("config/research.yaml"))
    p.add_argument("--inbox", type=Path, default=Path("research/manual_inbox"))
    p.add_argument("--captures-root", type=Path, default=Path("research/captures"))
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--max-per-source", type=int, default=10)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rss", action="store_true", help="Capture RSS/blog sources only")
    p.add_argument("--youtube", action="store_true", help="Capture YouTube sources only")
    p.add_argument("--arxiv", action="store_true", help="Capture arXiv sources only")
    p.add_argument("--all", action="store_true", help="Capture all source types")
    args = p.parse_args()

    from trading_lab.agent import lifecycle, source_capture

    lifecycle.init_db(args.db)

    explicit_modes = args.rss or args.youtube or args.arxiv
    enable_rss = args.all or args.rss or not explicit_modes
    enable_youtube = args.all or args.youtube or not explicit_modes
    enable_arxiv = args.all or args.arxiv or not explicit_modes

    result = source_capture.capture_sources(
        sources_path=args.sources,
        inbox_dir=args.inbox,
        captures_root=args.captures_root,
        db_path=args.db,
        enable_rss=enable_rss,
        enable_youtube=enable_youtube,
        enable_arxiv=enable_arxiv,
        dry_run=args.dry_run,
        max_items_per_source=args.max_per_source,
    )
    print(json.dumps(result))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
