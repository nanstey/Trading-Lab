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
