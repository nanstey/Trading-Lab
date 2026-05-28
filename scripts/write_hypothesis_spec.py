#!/usr/bin/env python3
"""Materialize a strict hypothesis-spec template.

Two modes:
  - default / --scaffold : write spec.md skeleton with all required sections
    as TODO. Refuses if memo.md is missing (memo is the upstream artifact).
    Leaves ingestion row at MEMO_READY/IN_PROGRESS.
  - --finalize : validate spec.md against trading_lab.agent.spec_validation.
    If valid, advance ingestion to SPEC_READY/PENDING.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent import ingestion  # noqa: E402
from trading_lab.agent.spec_validation import REQUIRED_SECTIONS, validate_spec_markdown  # noqa: E402


def _spec_template(item: ingestion.IngestionItem) -> str:
    name = item.thesis_name or item.capture_slug
    slug = item.thesis_slug or item.capture_slug
    fm = [
        "---",
        "artifact_type: hypothesis_spec",
        f"intake_id: {item.intake_id}",
        f"capture_slug: {item.capture_slug}",
        f"thesis_name: {name}",
        f"thesis_slug: {slug}",
        f"source_title: {item.source_title}",
        f"source_url: {item.source_url}",
        f"raw_capture_path: {item.raw_capture_path}",
        f"upstream_artifact: {item.folder_path}/memo.md",
        "recommended_next_action: discover",
        "---",
        "",
    ]
    body = [f"# {name}", ""]
    body.append(f"Parent dossier: `{item.folder_path}/dossier.md`")
    body.append(f"Parent memo: `{item.folder_path}/memo.md`")
    body.append(f"Raw capture: `{item.raw_capture_path}`")
    body.append("")
    for section in REQUIRED_SECTIONS:
        body.append(f"## {section}")
        body.append("TODO — make this concrete; no vague 'use indicators' language.")
        body.append("")
    return "\n".join(fm + body).rstrip() + "\n"


def run(
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
    spec_path = folder / "spec.md"
    if not memo_path.exists():
        raise FileNotFoundError(f"memo.md missing for slug={slug}; run distill_source_material first")

    if not finalize:
        if spec_path.exists() and not force:
            return {
                "ok": True,
                "intake_id": item.intake_id,
                "spec": str(spec_path),
                "skipped": "spec_already_exists",
            }
        spec_path.write_text(_spec_template(item))
        if item.stage in (ingestion.Stage.MEMO_READY.value, ingestion.Stage.DOSSIER_READY.value):
            ingestion.advance_stage(
                item.intake_id,
                ingestion.Stage.MEMO_READY.value,
                status=ingestion.Status.IN_PROGRESS.value,
                actor=actor,
                action="spec_scaffolded",
                next_action="fill_spec_sections",
                details={"spec_path": str(spec_path)},
                db_path=db_path,
            )
        return {
            "ok": True,
            "intake_id": item.intake_id,
            "spec": str(spec_path),
            "scaffolded": True,
        }

    if not spec_path.exists():
        raise FileNotFoundError(f"spec.md missing for slug={slug}")
    validation = validate_spec_markdown(spec_path.read_text())
    if not validation.is_valid:
        return {
            "ok": False,
            "intake_id": item.intake_id,
            "spec": str(spec_path),
            "reason": validation.reason,
            "missing_sections": list(validation.missing_sections),
            "empty_sections": list(validation.empty_sections),
        }
    ingestion.advance_stage(
        item.intake_id,
        ingestion.Stage.SPEC_READY.value,
        status=ingestion.Status.PENDING.value,
        actor=actor,
        action="spec_finalized",
        next_action="discover",
        details={"spec_path": str(spec_path)},
        db_path=db_path,
    )
    return {
        "ok": True,
        "intake_id": item.intake_id,
        "spec": str(spec_path),
        "finalized": True,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--actor", default=f"agent:spec:{os.environ.get('USER','unknown')}")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--scaffold", action="store_true", help="(default) write spec.md template")
    mode.add_argument("--finalize", action="store_true", help="validate spec.md and advance to SPEC_READY")
    p.add_argument("--force", action="store_true", help="Overwrite existing spec.md (scaffold mode)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = run(
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
            print(f"spec: READY  {args.slug} -> {result['spec']}")
        elif result.get("scaffolded"):
            print(f"spec: SCAFFOLD  {args.slug} -> {result['spec']}")
        elif result.get("skipped"):
            print(f"spec: SKIP  {args.slug}  ({result['skipped']})")
        elif not result.get("ok"):
            print(f"spec: BLOCKED  {args.slug}  {result.get('reason')}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
