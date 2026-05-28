"""Unit tests for the ingestion queue module."""

from __future__ import annotations

import pytest

from trading_lab.agent import ingestion


@pytest.fixture
def db(tmp_path):
    return tmp_path / "experiments.db"


def _record(db, **overrides):
    defaults = dict(
        source_url="https://example.com/idea-1",
        source_type="youtube:test",
        source_title="Idea One",
        capture_slug="idea-one",
        folder_path="research/hypotheses/idea-one",
        raw_capture_path="research/captures/raw/youtube/test/2026-01-01/abc.json",
        actor="test",
    )
    defaults.update(overrides)
    return ingestion.record_intake(db_path=db, **defaults)


def test_record_intake_inserts_and_logs_event(db):
    intake_id = _record(db)
    assert intake_id > 0
    item = ingestion.get(intake_id, db_path=db)
    assert item is not None
    assert item.stage == ingestion.Stage.CAPTURED.value
    assert item.status == ingestion.Status.PENDING.value
    assert item.capture_slug == "idea-one"
    hist = ingestion.history(intake_id, db_path=db)
    assert len(hist) == 1
    assert hist[0]["action"] == "intake_recorded"
    assert hist[0]["to_stage"] == ingestion.Stage.CAPTURED.value


def test_record_intake_is_idempotent_on_source_url(db):
    first = _record(db)
    second = _record(db)
    assert first == second
    assert len(ingestion.history(first, db_path=db)) == 1


def test_advance_stage_updates_and_logs(db):
    intake_id = _record(db)
    ingestion.advance_stage(
        intake_id,
        ingestion.Stage.DOSSIER_READY.value,
        actor="agent:dossier",
        db_path=db,
    )
    item = ingestion.get(intake_id, db_path=db)
    assert item is not None
    assert item.stage == ingestion.Stage.DOSSIER_READY.value
    assert item.status == ingestion.Status.PENDING.value
    hist = ingestion.history(intake_id, db_path=db)
    assert len(hist) == 2
    assert hist[1]["from_stage"] == ingestion.Stage.CAPTURED.value
    assert hist[1]["to_stage"] == ingestion.Stage.DOSSIER_READY.value


def test_advance_stage_unknown_intake_raises(db):
    with pytest.raises(ValueError):
        ingestion.advance_stage(99999, ingestion.Stage.DOSSIER_READY.value, actor="test", db_path=db)


def test_set_thesis_identity_records_rename(db):
    intake_id = _record(db)
    ingestion.set_thesis_identity(
        intake_id,
        thesis_name="Noise Print Snapback",
        thesis_slug="noise-print-snapback",
        folder_path="research/hypotheses/noise-print-snapback",
        actor="agent:naming",
        codename="Rubberband",
        db_path=db,
    )
    item = ingestion.get(intake_id, db_path=db)
    assert item is not None
    assert item.thesis_slug == "noise-print-snapback"
    assert item.thesis_name == "Noise Print Snapback"
    assert item.folder_path == "research/hypotheses/noise-print-snapback"


def test_get_by_slug_prefers_thesis_slug(db):
    intake_id = _record(db, capture_slug="raw-yt-title", source_url="https://example.com/a")
    ingestion.set_thesis_identity(
        intake_id,
        thesis_name="Real Strategy",
        thesis_slug="real-strategy",
        folder_path="research/hypotheses/real-strategy",
        actor="test",
        db_path=db,
    )
    by_thesis = ingestion.get_by_slug("real-strategy", db_path=db)
    by_capture = ingestion.get_by_slug("raw-yt-title", db_path=db)
    assert by_thesis is not None and by_thesis.intake_id == intake_id
    assert by_capture is not None and by_capture.intake_id == intake_id


def test_next_pending_picks_oldest(db):
    a = _record(db, source_url="https://example.com/a", capture_slug="a")
    b = _record(db, source_url="https://example.com/b", capture_slug="b")
    ingestion.advance_stage(a, ingestion.Stage.DOSSIER_READY.value, actor="t", db_path=db)
    ingestion.advance_stage(b, ingestion.Stage.DOSSIER_READY.value, actor="t", db_path=db)
    pick = ingestion.next_pending(ingestion.Stage.DOSSIER_READY.value, db_path=db)
    assert pick is not None and pick.intake_id == a


def test_list_items_filters_by_stage_and_status(db):
    a = _record(db, source_url="https://example.com/a", capture_slug="a")
    _record(db, source_url="https://example.com/b", capture_slug="b")
    ingestion.advance_stage(
        a,
        ingestion.Stage.SPEC_READY.value,
        status=ingestion.Status.DONE.value,
        actor="t",
        db_path=db,
    )
    captured_pending = ingestion.list_items(
        stage=ingestion.Stage.CAPTURED.value,
        status=ingestion.Status.PENDING.value,
        db_path=db,
    )
    spec_done = ingestion.list_items(
        stage=ingestion.Stage.SPEC_READY.value,
        status=ingestion.Status.DONE.value,
        db_path=db,
    )
    assert [i.capture_slug for i in captured_pending] == ["b"]
    assert [i.capture_slug for i in spec_done] == ["a"]
