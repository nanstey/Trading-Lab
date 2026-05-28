#!/usr/bin/env python3
"""Capture one supported source URL into the research funnel, optionally through PROPOSED."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in {"youtube.com", "m.youtube.com", "youtu.be"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--inbox", type=Path, default=Path("research/manual_inbox"))
    p.add_argument("--hypotheses-dir", type=Path, default=Path("research/hypotheses"))
    p.add_argument("--captures-root", type=Path, default=Path("research/captures"))
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--source-name", default="manual-link")
    p.add_argument("--actor", default=f"agent:ingest:{os.environ.get('USER', 'unknown')}")
    p.add_argument(
        "--discover",
        action="store_true",
        help="Immediately register the captured candidate into PROPOSED via the canonical discovery write path.",
    )
    args = p.parse_args()

    from trading_lab.agent import discovery, source_capture

    if not _is_youtube_url(args.url):
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "validate",
                    "error": "unsupported_url",
                    "supported": ["youtube"],
                    "url": args.url,
                }
            )
        )
        return 2

    capture = source_capture.capture_youtube_url(
        args.url,
        inbox_dir=args.inbox,
        captures_root=args.captures_root,
        db_path=args.db,
        source_name=args.source_name,
    )
    result: dict[str, object] = {
        "ok": bool(capture.get("ok")),
        "stage": "capture",
        "url": args.url,
        "capture": capture,
        "discovered": None,
    }

    if not capture.get("ok"):
        print(json.dumps(result))
        return 1

    if int(capture.get("pending_written", 0)) == 0 or not args.discover:
        print(json.dumps(result))
        return 0

    details = capture.get("details") or []
    slug = str(details[0].get("slug")) if details else ""
    candidates = discovery.scan_inbox(args.inbox, db_path=args.db)
    candidate = next((cand for cand in candidates if cand.slug == slug), None)
    if candidate is None:
        result.update(
            {
                "ok": False,
                "stage": "discover",
                "error": "captured_candidate_not_found_in_inbox",
                "slug": slug,
            }
        )
        print(json.dumps(result))
        return 1

    registered = discovery.register_candidate(
        candidate,
        db_path=args.db,
        hypotheses_dir=args.hypotheses_dir,
        actor=args.actor,
        inbox_dir=args.inbox,
    )
    result.update({"stage": "discover", "discovered": registered})
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
