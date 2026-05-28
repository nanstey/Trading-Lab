#!/usr/bin/env python3
"""Distill a source dossier into a venue-aware idea memo.

Two modes:
  - default / --scaffold : write a memo.md template stub (idempotent; only
    writes if missing or --force). Leaves the ingestion row at
    DOSSIER_READY/IN_PROGRESS so the operator/agent runbook knows the
    memo is being authored.
  - --finalize : check that memo.md exists and required sections are
    non-trivial, then advance the ingestion row to MEMO_READY/PENDING.

Required sections (must be filled before --finalize succeeds):
  Claimed edge, Polymarket fit, Polymarket failure modes, Required
  observables, Execution assumptions, Source-to-binary mapping,
  Fast reject reasons, Recommended disposition.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent import ingestion  # noqa: E402

REQUIRED_MEMO_SECTIONS: tuple[str, ...] = (
    "Claimed edge",
    "Polymarket fit",
    "Polymarket failure modes",
    "Required observables",
    "Execution assumptions",
    "Source-to-binary mapping",
    "Fast reject reasons",
    "Recommended disposition",
)


def _memo_template(item: ingestion.IngestionItem) -> str:
    fm = [
        "---",
        "artifact_type: idea_memo",
        f"intake_id: {item.intake_id}",
        f"capture_slug: {item.capture_slug}",
        f"thesis_name: {item.thesis_name}",
        f"thesis_slug: {item.thesis_slug}",
        f"source_title: {item.source_title}",
        f"source_url: {item.source_url}",
        f"raw_capture_path: {item.raw_capture_path}",
        f"upstream_artifact: {item.folder_path}/dossier.md",
        "recommended_next_action: assign_thesis_name",
        "---",
        "",
    ]
    body = [f"# Idea memo — {item.capture_slug}", ""]
    body.append(f"Upstream dossier: `{item.folder_path}/dossier.md`")
    body.append("")
    for section in REQUIRED_MEMO_SECTIONS:
        body.append(f"## {section}")
        body.append("TODO — replace with concrete content.")
        body.append("")
    return "\n".join(fm + body).rstrip() + "\n"


def _section_bodies(markdown: str) -> dict[str, str]:
    """Cheap heading parser shared with spec_validation but inlined to avoid coupling."""
    import re
    matches = list(re.finditer(r"^(?:#{1,6})\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
    if not matches:
        return {}
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        out[m.group(1).strip().lower()] = markdown[start:end].strip()
    return out


def _memo_is_filled(memo: str) -> tuple[bool, list[str]]:
    bodies = _section_bodies(memo)
    bad: list[str] = []
    for section in REQUIRED_MEMO_SECTIONS:
        body = bodies.get(section.lower(), "")
        if not body or body.startswith("TODO") or len(body) < 15:
            bad.append(section)
    return (not bad, bad)


def distill(
    *,
    slug: str,
    db_path: Path,
    actor: str,
    finalize: bool,
    force: bool,
) -> dict:
    item = ingestion.get_by_slug(slug, db_path=db_path)
    if item is None:
        raise ValueError(f"no ingestion row for slug={slug}")
    if item.stage in (ingestion.Stage.REJECTED_SOURCE.value, ingestion.Stage.SHELVED_SOURCE.value):
        raise ValueError(f"ingestion row is terminal ({item.stage}); refusing")

    folder = Path(item.folder_path)
    memo_path = folder / "memo.md"
    dossier_path = folder / "dossier.md"
    if not dossier_path.exists():
        raise FileNotFoundError(f"dossier missing for slug={slug}: {dossier_path}")

    if not finalize:
        if memo_path.exists() and not force:
            return {
                "ok": True,
                "intake_id": item.intake_id,
                "memo": str(memo_path),
                "skipped": "memo_already_exists",
            }
        folder.mkdir(parents=True, exist_ok=True)
        memo_path.write_text(_memo_template(item))
        if item.stage == ingestion.Stage.DOSSIER_READY.value:
            ingestion.advance_stage(
                item.intake_id,
                ingestion.Stage.DOSSIER_READY.value,
                status=ingestion.Status.IN_PROGRESS.value,
                actor=actor,
                action="memo_scaffolded",
                next_action="fill_memo_sections",
                details={"memo_path": str(memo_path)},
                db_path=db_path,
            )
        return {
            "ok": True,
            "intake_id": item.intake_id,
            "memo": str(memo_path),
            "scaffolded": True,
        }

    if not memo_path.exists():
        raise FileNotFoundError(f"memo.md missing for slug={slug}: {memo_path}")
    filled, bad = _memo_is_filled(memo_path.read_text())
    if not filled:
        return {
            "ok": False,
            "intake_id": item.intake_id,
            "memo": str(memo_path),
            "missing_or_empty_sections": bad,
        }
    ingestion.advance_stage(
        item.intake_id,
        ingestion.Stage.MEMO_READY.value,
        status=ingestion.Status.PENDING.value,
        actor=actor,
        action="memo_finalized",
        next_action="assign_thesis_name",
        details={"memo_path": str(memo_path)},
        db_path=db_path,
    )
    return {
        "ok": True,
        "intake_id": item.intake_id,
        "memo": str(memo_path),
        "finalized": True,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True, help="capture_slug or thesis_slug")
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--actor", default=f"agent:distill:{os.environ.get('USER','unknown')}")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--scaffold", action="store_true", help="(default) write memo.md template")
    mode.add_argument("--finalize", action="store_true", help="advance to MEMO_READY if memo is filled")
    p.add_argument("--force", action="store_true", help="Overwrite existing memo.md (scaffold mode)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = distill(
        slug=args.slug,
        db_path=args.db,
        actor=args.actor,
        finalize=args.finalize,
        force=args.force,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("finalized"):
            print(f"memo: READY  {args.slug} -> {result['memo']}")
        elif result.get("scaffolded"):
            print(f"memo: SCAFFOLD  {args.slug} -> {result['memo']}")
        elif result.get("skipped"):
            print(f"memo: SKIP  {args.slug}  ({result['skipped']})")
        elif not result.get("ok"):
            print(f"memo: BLOCKED  {args.slug}  missing/empty: {', '.join(result.get('missing_or_empty_sections', []))}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
