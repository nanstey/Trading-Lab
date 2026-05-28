#!/usr/bin/env python3
"""One-shot migration: flat research/ layout -> folder-per-idea.

For each `research/hypotheses/<slug>.md`:
  - classify by spec validation
  - move to `research/hypotheses/<slug>/spec.md` (spec-grade) or `dossier.md` (everything else)
  - insert ingestion_items row at the matching stage

For each `research/manual_inbox/<slug>.md`:
  - move to `research/hypotheses/<slug>/dossier.md` (if no collision)
  - insert ingestion_items row at DOSSIER_READY/PENDING

For each `research/paper_reports/<slug>_<YYYYMMDD>.md`:
  - move to `research/hypotheses/<slug>/paper_reports/<YYYYMMDD>.md`

Dry-run by default. Pass --apply to commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent import ingestion  # noqa: E402
from trading_lab.agent.spec_validation import validate_spec_markdown  # noqa: E402


@dataclass
class HypothesisFile:
    path: Path
    slug: str
    frontmatter: dict[str, str]
    body: str
    is_spec: bool
    spec_reason: str


@dataclass
class InboxFile:
    path: Path
    slug: str
    frontmatter: dict[str, str]


@dataclass
class PaperReportFile:
    path: Path
    slug: str
    date_str: str


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_lines = text[3:end].strip().splitlines()
    body = text[end + 4 :].lstrip("\n")
    out: dict[str, str] = {}
    for line in fm_lines:
        if ":" in line and not line.lstrip().startswith("-"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out, body


def _collect_hypotheses(root: Path) -> list[HypothesisFile]:
    out: list[HypothesisFile] = []
    if not root.exists():
        return out
    for path in sorted(root.iterdir()):
        if path.is_dir() or path.suffix != ".md":
            continue
        text = path.read_text()
        fm, body = _parse_frontmatter(text)
        validation = validate_spec_markdown(text)
        out.append(
            HypothesisFile(
                path=path,
                slug=path.stem,
                frontmatter=fm,
                body=body,
                is_spec=validation.is_valid,
                spec_reason=validation.reason,
            )
        )
    return out


def _collect_inbox(root: Path) -> list[InboxFile]:
    out: list[InboxFile] = []
    if not root.exists():
        return out
    for path in sorted(root.glob("*.md")):
        text = path.read_text()
        fm, _ = _parse_frontmatter(text)
        out.append(InboxFile(path=path, slug=path.stem, frontmatter=fm))
    return out


_PAPER_REPORT_RE = re.compile(r"^(?P<slug>.+)_(?P<date>\d{8})$")


def _collect_paper_reports(root: Path) -> list[PaperReportFile]:
    out: list[PaperReportFile] = []
    if not root.exists():
        return out
    for path in sorted(root.glob("*.md")):
        m = _PAPER_REPORT_RE.match(path.stem)
        if not m:
            continue
        out.append(PaperReportFile(path=path, slug=m.group("slug"), date_str=m.group("date")))
    return out


def _index_raw_captures(captures_root: Path) -> dict[str, Path]:
    """Build a {source_url -> raw_capture_path} map by reading raw JSON archives."""
    index: dict[str, Path] = {}
    if not captures_root.exists():
        return index
    for json_path in captures_root.rglob("*.json"):
        try:
            data = json.loads(json_path.read_text())
        except Exception:
            continue
        url = str(data.get("url") or "").strip()
        if url and url not in index:
            index[url] = json_path
    return index


def _resolve_raw_capture(
    fm: dict[str, str],
    raw_index: dict[str, Path],
) -> Path | None:
    explicit = (fm.get("raw_capture_path") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
    source_url = (fm.get("source_url") or "").strip()
    if source_url and source_url in raw_index:
        return raw_index[source_url]
    return None


def _ensure_dir(p: Path, apply: bool) -> None:
    if apply:
        p.mkdir(parents=True, exist_ok=True)


def _move(src: Path, dst: Path, apply: bool) -> None:
    if not apply:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    shutil.move(str(src), str(dst))


def migrate(
    *,
    research_root: Path,
    db_path: Path,
    actor: str,
    apply: bool,
) -> dict[str, object]:
    hypotheses_root = research_root / "hypotheses"
    inbox_root = research_root / "manual_inbox"
    paper_reports_root = research_root / "paper_reports"
    captures_root = research_root / "captures" / "raw"

    raw_index = _index_raw_captures(captures_root)
    hypotheses = _collect_hypotheses(hypotheses_root)
    inbox = _collect_inbox(inbox_root)
    reports = _collect_paper_reports(paper_reports_root)

    actions: list[dict[str, object]] = []
    intake_ids: dict[str, int] = {}  # slug -> intake_id (capture_slug)

    for hyp in hypotheses:
        target_dir = hypotheses_root / hyp.slug
        if hyp.is_spec:
            artifact = "spec.md"
            stage = ingestion.Stage.DISCOVERED.value
            status = ingestion.Status.DONE.value
        else:
            artifact = "dossier.md"
            stage = ingestion.Stage.DOSSIER_READY.value
            status = ingestion.Status.PENDING.value
        dst = target_dir / artifact
        raw_path = _resolve_raw_capture(hyp.frontmatter, raw_index)
        source_url = hyp.frontmatter.get("source_url") or f"local:hypothesis/{hyp.slug}"
        source_type = hyp.frontmatter.get("source") or "legacy"
        actions.append(
            {
                "kind": "migrate_hypothesis",
                "slug": hyp.slug,
                "from": str(hyp.path),
                "to": str(dst),
                "stage": stage,
                "status": status,
                "is_spec": hyp.is_spec,
                "spec_reason": hyp.spec_reason,
                "source_url": source_url,
                "raw_capture_path": str(raw_path) if raw_path else "",
            }
        )
        if apply:
            _ensure_dir(target_dir, apply)
            _move(hyp.path, dst, apply)
            intake_id = ingestion.record_intake(
                source_url=source_url,
                source_type=source_type,
                source_title=hyp.frontmatter.get("title") or hyp.slug,
                capture_slug=hyp.slug,
                folder_path=str(target_dir),
                raw_capture_path=str(raw_path) if raw_path else None,
                stage=ingestion.Stage.CAPTURED.value,
                status=ingestion.Status.PENDING.value,
                actor=actor,
                db_path=db_path,
            )
            if stage != ingestion.Stage.CAPTURED.value:
                ingestion.advance_stage(
                    intake_id,
                    stage,
                    status=status,
                    actor=actor,
                    action="migrate_from_flat_hypothesis",
                    details={"artifact": artifact, "is_spec": hyp.is_spec},
                    db_path=db_path,
                )
            if hyp.is_spec:
                ingestion.set_thesis_identity(
                    intake_id,
                    thesis_name=hyp.frontmatter.get("title") or hyp.slug,
                    thesis_slug=hyp.slug,
                    folder_path=str(target_dir),
                    actor=actor,
                    db_path=db_path,
                )
            intake_ids[hyp.slug] = intake_id

    for ib in inbox:
        target_dir = hypotheses_root / ib.slug
        dst = target_dir / "dossier.md"
        if dst.exists() or any(a.get("to") == str(dst) for a in actions):
            actions.append(
                {
                    "kind": "skip_inbox_collision",
                    "slug": ib.slug,
                    "reason": "dossier.md already produced from hypotheses/",
                    "from": str(ib.path),
                }
            )
            continue
        raw_path = _resolve_raw_capture(ib.frontmatter, raw_index)
        source_url = ib.frontmatter.get("source_url") or f"local:inbox/{ib.slug}"
        source_type = ib.frontmatter.get("source") or "manual_inbox"
        actions.append(
            {
                "kind": "migrate_inbox",
                "slug": ib.slug,
                "from": str(ib.path),
                "to": str(dst),
                "stage": ingestion.Stage.DOSSIER_READY.value,
                "status": ingestion.Status.PENDING.value,
                "source_url": source_url,
                "raw_capture_path": str(raw_path) if raw_path else "",
            }
        )
        if apply:
            _ensure_dir(target_dir, apply)
            _move(ib.path, dst, apply)
            intake_id = ingestion.record_intake(
                source_url=source_url,
                source_type=source_type,
                source_title=ib.frontmatter.get("title") or ib.slug,
                capture_slug=ib.slug,
                folder_path=str(target_dir),
                raw_capture_path=str(raw_path) if raw_path else None,
                stage=ingestion.Stage.CAPTURED.value,
                status=ingestion.Status.PENDING.value,
                actor=actor,
                db_path=db_path,
            )
            ingestion.advance_stage(
                intake_id,
                ingestion.Stage.DOSSIER_READY.value,
                status=ingestion.Status.PENDING.value,
                actor=actor,
                action="migrate_from_manual_inbox",
                db_path=db_path,
            )
            intake_ids[ib.slug] = intake_id

    for rep in reports:
        target_dir = hypotheses_root / rep.slug / "paper_reports"
        dst = target_dir / f"{rep.date_str}.md"
        actions.append(
            {
                "kind": "migrate_paper_report",
                "slug": rep.slug,
                "from": str(rep.path),
                "to": str(dst),
            }
        )
        if apply:
            _ensure_dir(target_dir, apply)
            _move(rep.path, dst, apply)

    if apply:
        if inbox_root.exists() and not any(p.suffix == ".md" for p in inbox_root.iterdir()):
            shutil.rmtree(inbox_root)
        if paper_reports_root.exists() and not any(paper_reports_root.iterdir()):
            paper_reports_root.rmdir()

    return {
        "applied": apply,
        "actions": actions,
        "counts": {
            "hypotheses": len(hypotheses),
            "inbox": len(inbox),
            "paper_reports": len(reports),
        },
    }


def _print_summary(result: dict[str, object]) -> None:
    actions = result.get("actions") or []
    print(f"\n== migration summary ({'APPLIED' if result.get('applied') else 'DRY-RUN'}) ==")
    print(f"hypotheses: {result['counts']['hypotheses']}  "
          f"inbox: {result['counts']['inbox']}  "
          f"paper_reports: {result['counts']['paper_reports']}\n")
    for a in actions:
        if a["kind"] == "migrate_hypothesis":
            kind = "SPEC" if a["is_spec"] else "DOSSIER"
            print(f"  hypothesis -> {kind:7s}  {a['slug']}")
            if not a["is_spec"]:
                print(f"      reason: {a['spec_reason']}")
        elif a["kind"] == "migrate_inbox":
            print(f"  inbox      -> DOSSIER  {a['slug']}")
        elif a["kind"] == "skip_inbox_collision":
            print(f"  inbox      -> SKIP     {a['slug']}  (collision)")
        elif a["kind"] == "migrate_paper_report":
            print(f"  paper      -> MOVE     {a['slug']}/paper_reports/{Path(a['to']).name}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--research-root", type=Path, default=Path("research"))
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument("--actor", default=f"agent:migrate:{os.environ.get('USER','unknown')}")
    p.add_argument("--apply", action="store_true", help="Commit moves and DB rows (default: dry-run)")
    p.add_argument("--json", action="store_true", help="Print machine-readable result")
    args = p.parse_args()

    result = migrate(
        research_root=args.research_root,
        db_path=args.db,
        actor=args.actor,
        apply=args.apply,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
