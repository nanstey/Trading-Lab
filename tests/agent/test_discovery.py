"""Unit tests for discovery intake helpers."""

from __future__ import annotations

import pytest

from trading_lab.agent import discovery, lifecycle


@pytest.fixture(autouse=True)
def _isolate_events(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "trading_lab.agent.events.DEFAULT_EVENTS_PATH",
        tmp_path / "events.jsonl",
    )


@pytest.fixture
def db(tmp_path):
    return tmp_path / "experiments.db"


def test_register_candidate_writes_hypothesis_db_and_archives_inbox(tmp_path, db):
    lifecycle.init_db(db)
    inbox_dir = tmp_path / "manual_inbox"
    inbox_dir.mkdir()
    (inbox_dir / "my-edge.md").write_text("placeholder")

    candidate = discovery.Candidate(
        slug="my-edge",
        summary="# Mean-reversion edge\n\nWait for dislocations and fade extremes.",
        source_url="https://youtu.be/example12345",
        source_type="youtube:telegram-auto-ingest",
        prior_attempts=["crowded_signal"],
        dedup_candidates=["older-edge"],
        market_criteria={"venue": "polymarket"},
    )

    registered = discovery.register_candidate(
        candidate,
        db_path=db,
        hypotheses_dir=tmp_path / "hypotheses",
        actor="agent:test",
        inbox_dir=inbox_dir,
    )

    assert (tmp_path / "hypotheses" / "my-edge.md").exists()
    assert registered["slug"] == "my-edge"
    assert registered["archived_to"] is not None
    assert not (inbox_dir / "my-edge.md").exists()
    assert list((inbox_dir / ".archived").rglob("my-edge.md"))

    hypothesis = lifecycle.get_hypothesis("my-edge", db_path=db)
    assert hypothesis is not None
    assert hypothesis.state == lifecycle.State.PROPOSED.value
    assert hypothesis.source_url == "https://youtu.be/example12345"


def _write_valid_spec(folder, slug, name):
    from trading_lab.agent.spec_validation import REQUIRED_SECTIONS
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"# {name}", ""]
    for section in REQUIRED_SECTIONS:
        lines.append(f"## {section}")
        lines.append(f"Concrete content for {section} that is at least one sentence long.")
        lines.append("")
    (folder / "spec.md").write_text("\n".join(lines))


def test_register_from_ingestion_promotes_to_proposed(tmp_path, db):
    from trading_lab.agent import ingestion

    lifecycle.init_db(db)
    folder = tmp_path / "hypotheses" / "noise-print-snapback"
    _write_valid_spec(folder, "noise-print-snapback", "Noise Print Snapback")

    intake_id = ingestion.record_intake(
        source_url="https://example.com/source-a",
        source_type="youtube:test",
        source_title="Source A",
        capture_slug="source-a-title",
        folder_path=str(folder),
        raw_capture_path="research/captures/raw/youtube/test/2026-01-01/a.json",
        actor="test",
        db_path=db,
    )
    ingestion.set_thesis_identity(
        intake_id,
        thesis_name="Noise Print Snapback",
        thesis_slug="noise-print-snapback",
        folder_path=str(folder),
        actor="test",
        db_path=db,
    )
    ingestion.advance_stage(
        intake_id,
        ingestion.Stage.SPEC_READY.value,
        actor="test",
        db_path=db,
    )

    result = discovery.register_from_ingestion(
        intake_id,
        db_path=db,
        hypotheses_dir=tmp_path / "hypotheses",
        actor="agent:test",
    )

    assert result["thesis_slug"] == "noise-print-snapback"
    hyp = lifecycle.get_hypothesis("noise-print-snapback", db_path=db)
    assert hyp is not None and hyp.state == lifecycle.State.PROPOSED.value
    item = ingestion.get(intake_id, db_path=db)
    assert item is not None
    assert item.stage == ingestion.Stage.DISCOVERED.value
    assert item.status == ingestion.Status.DONE.value


def test_register_from_ingestion_rejects_invalid_spec(tmp_path, db):
    from trading_lab.agent import ingestion

    lifecycle.init_db(db)
    folder = tmp_path / "hypotheses" / "half-baked"
    folder.mkdir(parents=True)
    (folder / "spec.md").write_text("# half-baked\n\n## Hypothesis\nTODO\n")

    intake_id = ingestion.record_intake(
        source_url="https://example.com/half",
        source_type="manual",
        source_title="Half",
        capture_slug="half-baked",
        folder_path=str(folder),
        actor="test",
        db_path=db,
    )
    ingestion.advance_stage(
        intake_id,
        ingestion.Stage.SPEC_READY.value,
        actor="test",
        db_path=db,
    )

    with pytest.raises(ValueError):
        discovery.register_from_ingestion(
            intake_id, db_path=db, hypotheses_dir=tmp_path / "hypotheses", actor="t"
        )
    item = ingestion.get(intake_id, db_path=db)
    assert item is not None and item.stage == ingestion.Stage.SPEC_READY.value
