"""Unit tests for the agent lifecycle module."""

from __future__ import annotations

import pytest

from nautilus_predict.agent import lifecycle


@pytest.fixture(autouse=True)
def _isolate_events(tmp_path, monkeypatch):
    """Each test gets a temp events.jsonl so lifecycle.transition doesn't
    write to the real `logs/events.jsonl`."""
    monkeypatch.setattr(
        "nautilus_predict.agent.events.DEFAULT_EVENTS_PATH",
        tmp_path / "events.jsonl",
    )


@pytest.fixture
def db(tmp_path):
    return tmp_path / "experiments.db"


def test_add_hypothesis_creates_initial_transition(db):
    h = lifecycle.add_hypothesis(
        "test-slug", state=lifecycle.State.PROPOSED.value,
        actor="user:tester", db_path=db,
    )
    assert h.slug == "test-slug"
    assert h.state == lifecycle.State.PROPOSED.value
    hist = lifecycle.history("test-slug", db_path=db)
    assert len(hist) == 1
    assert hist[0]["to_state"] == lifecycle.State.PROPOSED.value


def test_transition_appends_history(db):
    lifecycle.add_hypothesis("t", state=lifecycle.State.PROPOSED.value,
                             actor="user:tester", db_path=db)
    lifecycle.transition(
        "t", lifecycle.State.CODEGEN.value, "ready", "agent:codegen", db_path=db
    )
    hist = lifecycle.history("t", db_path=db)
    assert len(hist) == 2
    assert hist[-1]["from_state"] == lifecycle.State.PROPOSED.value
    assert hist[-1]["to_state"] == lifecycle.State.CODEGEN.value


def test_human_gated_refuses_agent(db):
    lifecycle.add_hypothesis(
        "t", state=lifecycle.State.PAPER_READY.value,
        actor="user:tester", db_path=db,
    )
    with pytest.raises(PermissionError):
        lifecycle.transition(
            "t", lifecycle.State.PAPER.value, "promote",
            actor="agent:wrong", db_path=db,
        )


def test_human_gated_accepts_user(db):
    lifecycle.add_hypothesis(
        "t", state=lifecycle.State.PAPER_READY.value,
        actor="user:tester", db_path=db,
    )
    lifecycle.transition(
        "t", lifecycle.State.PAPER.value, "promote",
        actor="user:tester", db_path=db,
    )
    h = lifecycle.get_hypothesis("t", db_path=db)
    assert h is not None
    assert h.state == lifecycle.State.PAPER.value


def test_transition_unknown_slug_raises(db):
    with pytest.raises(ValueError):
        lifecycle.transition(
            "nope", lifecycle.State.CODEGEN.value, "x", "user:t", db_path=db
        )


def test_record_experiment_returns_id(db):
    lifecycle.add_hypothesis("t", state=lifecycle.State.BACKTEST.value,
                             actor="user:tester", db_path=db)
    exp_id = lifecycle.record_experiment(
        slug="t",
        params={"foo": "bar"},
        data_start="2026-05-01", data_end="2026-05-10",
        sharpe=1.2, max_dd=-5.0, fill_rate=0.9,
        pnl=42.0, n_trades=120,
        db_path=db,
    )
    assert exp_id > 0
    rows = lifecycle.list_experiments("t", db_path=db)
    assert len(rows) == 1
    assert rows[0]["pnl"] == 42.0
