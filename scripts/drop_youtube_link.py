#!/usr/bin/env python3
"""Drop a single YouTube link into the manual link dropbox."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url")
    p.add_argument("--dropbox", type=Path, default=Path("research/link_dropbox"))
    args = p.parse_args()

    args.dropbox.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(args.url.encode("utf-8")).hexdigest()[:12]
    out_path = args.dropbox / f"youtube-{digest}.txt"
    out_path.write_text(args.url.strip() + "\n")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
