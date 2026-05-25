"""
Daily budget tracker — token spend, backtests, paper/live promotions.

Backed by `budget_ledger` in `research/experiments.db`. Every agentic
runbook should check budget before starting work; over-budget runbooks
return early and emit a JSON line for observability.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nautilus_predict.agent.lifecycle import DEFAULT_DB_PATH, SCHEMA


@dataclass(frozen=True)
class BudgetCaps:
    llm_tokens_per_day: int = 100_000
    backtests_per_day: int = 50
    paper_starts_per_week: int = 1
    live_starts_per_day: int = 0


DEFAULT_CAPS = BudgetCaps()


@contextmanager
def _open(path: Path = DEFAULT_DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
    finally:
        conn.close()


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _ensure_row(conn, day: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO budget_ledger (date) VALUES (?)", (day,)
    )


def consumed(db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """Return today's consumed counts (zeros if no row exists)."""
    day = _today()
    with _open(db_path) as conn:
        _ensure_row(conn, day)
        row = conn.execute(
            "SELECT llm_tokens, backtests, paper_starts, live_starts "
            "FROM budget_ledger WHERE date=?",
            (day,),
        ).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}  # type: ignore[index]


def consume(
    field: str,
    n: int = 1,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Increment a counter and return the post-update value."""
    if field not in ("llm_tokens", "backtests", "paper_starts", "live_starts"):
        raise ValueError(f"unknown budget field: {field}")
    day = _today()
    with _open(db_path) as conn:
        _ensure_row(conn, day)
        conn.execute(
            f"UPDATE budget_ledger SET {field} = COALESCE({field}, 0) + ? WHERE date=?",
            (n, day),
        )
        row = conn.execute(
            f"SELECT {field} FROM budget_ledger WHERE date=?", (day,)
        ).fetchone()
        return int(row[field] or 0)


def check(
    field: str,
    caps: BudgetCaps = DEFAULT_CAPS,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[bool, int, int]:
    """
    Return (ok, current, cap) for the given counter.

    `ok` is False when current >= cap.
    """
    counts = consumed(db_path)
    cap_map = {
        "llm_tokens": caps.llm_tokens_per_day,
        "backtests": caps.backtests_per_day,
        "paper_starts": caps.paper_starts_per_week,
        "live_starts": caps.live_starts_per_day,
    }
    cap = cap_map.get(field, 0)
    current = counts.get(field, 0)
    return current < cap, current, cap
