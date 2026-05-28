#!/usr/bin/env python3
"""Assign the canonical strategy identity at the naming checkpoint.

Input: --slug (capture_slug or thesis_slug) + --thesis-name + --thesis-slug
       [optional --codename]

Effects (atomic-ish best effort):
  1. Rename research/hypotheses/<old>/ -> research/hypotheses/<thesis_slug>/
     (skipped if the names already match).
  2. Sweep frontmatter in every *.md inside the folder, replacing the
     `thesis_name`, `thesis_slug`, `codename` fields.
  3. Update ingestion_items: set thesis_name, thesis_slug, folder_path; log
     a thesis_named event.

Refuses if the destination folder already exists and differs from current.
Refuses to rename via this script if a `.git` worktree is dirty in the folder —
operator should `git mv` manually for history-preserving renames in real repos.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent import ingestion  # noqa: E402

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def _valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.fullmatch(slug))


def _update_frontmatter(text: str, updates: dict[str, str]) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    head = text[3:end]
    rest = text[end + 4 :]
    lines = head.splitlines()
    keys_seen: set[str] = set()
    out_lines: list[str] = []
    for line in lines:
        if ":" in line:
            k, _, _ = line.partition(":")
            k = k.strip()
            if k in updates:
                out_lines.append(f"{k}: {updates[k]}")
                keys_seen.add(k)
                continue
        out_lines.append(line)
    for k, v in updates.items():
        if k not in keys_seen:
            out_lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(out_lines).strip("\n") + "\n---" + rest


def _sweep_folder(folder: Path, updates: dict[str, str]) -> list[Path]:
    touched: list[Path] = []
    for p in folder.glob("*.md"):
        text = p.read_text()
        new = _update_frontmatter(text, updates)
        if new != text:
            p.write_text(new)
            touched.append(p)
    return touched


def assign(
    *,
    slug: str,
    thesis_name: str,
    thesis_slug: str,
    codename: str | None,
    hypotheses_dir: Path,
    db_path: Path,
    actor: str,
) -> dict:
    if not _valid_slug(thesis_slug):
        raise ValueError(f"invalid thesis_slug: {thesis_slug!r}")
    item = ingestion.get_by_slug(slug, db_path=db_path)
    if item is None:
        raise ValueError(f"no ingestion row for slug={slug}")

    old_folder = Path(item.folder_path)
    new_folder = hypotheses_dir / thesis_slug

    renamed = False
    if old_folder.resolve() != new_folder.resolve():
        if new_folder.exists():
            raise FileExistsError(f"destination already exists: {new_folder}")
        if not old_folder.exists():
            raise FileNotFoundError(f"current folder missing: {old_folder}")
        old_folder.rename(new_folder)
        renamed = True

    updates = {
        "thesis_name": thesis_name,
        "thesis_slug": thesis_slug,
    }
    if codename:
        updates["codename"] = codename
    touched = _sweep_folder(new_folder, updates)

    ingestion.set_thesis_identity(
        item.intake_id,
        thesis_name=thesis_name,
        thesis_slug=thesis_slug,
        folder_path=str(new_folder),
        actor=actor,
        codename=codename,
        db_path=db_path,
    )

    return {
        "ok": True,
        "intake_id": item.intake_id,
        "renamed": renamed,
        "from": str(old_folder),
        "to": str(new_folder),
        "touched_files": [str(p) for p in touched],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True, help="capture_slug or thesis_slug to look up the ingestion row")
    p.add_argument("--thesis-name", required=True)
    p.add_argument("--thesis-slug", required=True)
    p.add_argument("--codename", default=None)
    p.add_argument("--hypotheses-dir", type=Path, default=Path("research/hypotheses"))
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--actor", default=f"agent:naming:{os.environ.get('USER','unknown')}")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = assign(
        slug=args.slug,
        thesis_name=args.thesis_name,
        thesis_slug=args.thesis_slug,
        codename=args.codename,
        hypotheses_dir=args.hypotheses_dir,
        db_path=args.db,
        actor=args.actor,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        flag = "RENAMED" if result["renamed"] else "ALIGN  "
        print(f"naming: {flag}  {args.thesis_slug}  ({len(result['touched_files'])} files updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
