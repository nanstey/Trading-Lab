"""
Lifecycle & experiment-DB module.

This is the ONLY module allowed to:
- INSERT INTO `lifecycle_transitions`
- UPDATE `hypotheses.state`

All other modules read freely but route writes through here. Centralising
state transitions makes auditability tractable (one place to inspect when
a strategy mysteriously ends up in a state it shouldn't be).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("research/experiments.db")


class State(StrEnum):
    """Lifecycle states. Allowed transitions are documented in the spec."""

    PROPOSED = "PROPOSED"
    CODEGEN = "CODEGEN"
    SMOKE_PASS = "SMOKE_PASS"
    BACKTEST = "BACKTEST"
    OPTIMIZE = "OPTIMIZE"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER_READY = "PAPER_READY"
    PAPER = "PAPER"
    LIVE_READY = "LIVE_READY"
    LIVE = "LIVE"
    HALTED = "HALTED"
    SHELVED = "SHELVED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


# Transitions that REQUIRE a human actor (no agent may perform these).
HUMAN_GATED = {
    (State.PAPER_READY, State.PAPER),
    (State.LIVE_READY, State.LIVE),
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    slug TEXT PRIMARY KEY,
    source_url TEXT,
    source_type TEXT,
    summary TEXT,
    summary_embedding BLOB,
    state TEXT NOT NULL,
    rejection_reason TEXT,
    rejection_category TEXT,
    parent_slug TEXT,
    created_at TEXT,
    updated_at TEXT,
    market_criteria_json TEXT,
    strategy_module TEXT,
    strategy_class TEXT,
    strategy_config_class TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT,
    params_json TEXT,
    data_start TEXT,
    data_end TEXT,
    sharpe REAL,
    max_dd REAL,
    fill_rate REAL,
    pnl REAL,
    n_trades INTEGER,
    walk_forward_oos_sharpe REAL,
    code_hash TEXT,
    data_hash TEXT,
    kill_switch_triggered INTEGER,
    created_at TEXT,
    FOREIGN KEY (slug) REFERENCES hypotheses(slug)
);

CREATE TABLE IF NOT EXISTS lifecycle_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT,
    from_state TEXT,
    to_state TEXT,
    reason TEXT,
    actor TEXT,
    timestamp TEXT,
    FOREIGN KEY (slug) REFERENCES hypotheses(slug)
);

CREATE TABLE IF NOT EXISTS budget_ledger (
    date TEXT PRIMARY KEY,
    llm_tokens INTEGER DEFAULT 0,
    backtests INTEGER DEFAULT 0,
    paper_starts INTEGER DEFAULT 0,
    live_starts INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ingestion_items (
    intake_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    source_title TEXT,
    capture_slug TEXT NOT NULL,
    thesis_name TEXT,
    thesis_slug TEXT,
    folder_path TEXT NOT NULL,
    raw_capture_path TEXT,
    stage TEXT NOT NULL DEFAULT 'CAPTURED',
    status TEXT NOT NULL DEFAULT 'PENDING',
    next_action TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intake_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT,
    action TEXT NOT NULL,
    details_json TEXT,
    FOREIGN KEY (intake_id) REFERENCES ingestion_items(intake_id)
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_state ON hypotheses(state);
CREATE INDEX IF NOT EXISTS idx_hypotheses_rejection ON hypotheses(rejection_category);
CREATE INDEX IF NOT EXISTS idx_experiments_slug ON experiments(slug);
CREATE INDEX IF NOT EXISTS idx_transitions_slug ON lifecycle_transitions(slug);
CREATE INDEX IF NOT EXISTS idx_ingestion_items_stage_status ON ingestion_items(stage, status);
CREATE INDEX IF NOT EXISTS idx_ingestion_items_thesis_slug ON ingestion_items(thesis_slug);
CREATE INDEX IF NOT EXISTS idx_ingestion_items_capture_slug ON ingestion_items(capture_slug);
CREATE INDEX IF NOT EXISTS idx_ingestion_events_intake ON ingestion_events(intake_id, id);
"""


@dataclass
class Hypothesis:
    slug: str
    state: str
    source_url: str = ""
    source_type: str = "manual"
    summary: str = ""
    rejection_reason: str | None = None
    rejection_category: str | None = None
    parent_slug: str | None = None
    created_at: str = ""
    updated_at: str = ""
    market_criteria: dict[str, Any] = field(default_factory=dict)
    strategy_module: str = ""
    strategy_class: str = ""
    strategy_config_class: str = ""

    @property
    def venue(self) -> str:
        """Venue this hypothesis runs against. Defaults to polymarket.

        Stored in `market_criteria["venue"]` (no schema migration needed).
        """
        v = (self.market_criteria.get("venue") or "polymarket").lower()
        return v


def init_db(path: Path = DEFAULT_DB_PATH) -> None:
    """Create the experiment DB schema. Idempotent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


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


def add_hypothesis(
    slug: str,
    state: str = State.PROPOSED.value,
    *,
    source_url: str = "",
    source_type: str = "manual",
    summary: str = "",
    parent_slug: str | None = None,
    market_criteria: dict[str, Any] | None = None,
    strategy_module: str = "",
    strategy_class: str = "",
    strategy_config_class: str = "",
    actor: str = "user",
    db_path: Path = DEFAULT_DB_PATH,
) -> Hypothesis:
    """
    Insert or refresh a hypothesis row.

    First-insert path: logs an initial `lifecycle_transitions` row with the
    requested state.

    Idempotent-update path: when the slug already exists, this preserves
    the current `state` and `created_at` and only refreshes metadata
    (`source_*`, `summary`, `market_criteria_json`, strategy refs,
    `updated_at`). Does not write a transition.
    """
    now = datetime.now(tz=UTC).isoformat()
    with _open(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT state FROM hypotheses WHERE slug=?", (slug,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO hypotheses "
                "(slug, source_url, source_type, summary, state, parent_slug, "
                " created_at, updated_at, market_criteria_json, "
                " strategy_module, strategy_class, strategy_config_class) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    slug, source_url, source_type, summary, state, parent_slug,
                    now, now, json.dumps(market_criteria or {}),
                    strategy_module, strategy_class, strategy_config_class,
                ),
            )
            conn.execute(
                "INSERT INTO lifecycle_transitions "
                "(slug, from_state, to_state, reason, actor, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (slug, None, state, "initial create", actor, now),
            )
        else:
            conn.execute(
                "UPDATE hypotheses SET "
                " source_url=?, source_type=?, summary=?, "
                " parent_slug=COALESCE(?, parent_slug), "
                " updated_at=?, "
                " market_criteria_json=?, "
                " strategy_module=?, strategy_class=?, strategy_config_class=? "
                "WHERE slug=?",
                (
                    source_url, source_type, summary, parent_slug, now,
                    json.dumps(market_criteria or {}),
                    strategy_module, strategy_class, strategy_config_class,
                    slug,
                ),
            )
        conn.execute("COMMIT")
    return get_hypothesis(slug, db_path=db_path)  # type: ignore[return-value]


def get_hypothesis(slug: str, db_path: Path = DEFAULT_DB_PATH) -> Hypothesis | None:
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM hypotheses WHERE slug=?", (slug,)
        ).fetchone()
    if not row:
        return None
    return _row_to_hypothesis(row)


def list_hypotheses(
    state: str | None = None,
    rejection_category: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[Hypothesis]:
    with _open(db_path) as conn:
        sql = "SELECT * FROM hypotheses WHERE 1=1"
        params: list[Any] = []
        if state:
            sql += " AND state=?"
            params.append(state)
        if rejection_category:
            sql += " AND rejection_category=?"
            params.append(rejection_category)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_hypothesis(r) for r in rows]


def transition(
    slug: str,
    to_state: str,
    reason: str,
    actor: str,
    rejection_category: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """
    Atomically transition a hypothesis to a new state.

    Refuses transitions in `HUMAN_GATED` unless actor starts with "user".
    """
    now = datetime.now(tz=UTC).isoformat()
    with _open(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT state FROM hypotheses WHERE slug=?", (slug,)
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            raise ValueError(f"unknown hypothesis: {slug}")
        from_state = row["state"]
        if (from_state, to_state) in HUMAN_GATED and not actor.startswith("user"):
            conn.execute("ROLLBACK")
            raise PermissionError(
                f"{from_state} → {to_state} is human-gated; actor={actor}"
            )
        conn.execute(
            "UPDATE hypotheses SET state=?, updated_at=?, "
            " rejection_reason=COALESCE(?, rejection_reason), "
            " rejection_category=COALESCE(?, rejection_category) "
            "WHERE slug=?",
            (
                to_state, now,
                reason if to_state == State.REJECTED.value else None,
                rejection_category,
                slug,
            ),
        )
        conn.execute(
            "INSERT INTO lifecycle_transitions "
            "(slug, from_state, to_state, reason, actor, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (slug, from_state, to_state, reason, actor, now),
        )
        conn.execute("COMMIT")

    # Emit structured event so the operator agent / SMS bridge can pick it up.
    # Severity escalates for promotions, halts, retirements.
    from trading_lab.agent.events import emit_event

    sev = "info"
    promoted = to_state in (State.PAPER.value, State.LIVE.value)
    halted = to_state in (State.HALTED.value, State.RETIRED.value)
    if promoted or halted:
        sev = "warn" if promoted else "critical"
    emit_event(
        type="lifecycle_transition",
        summary=f"{slug}: {from_state} → {to_state} ({actor})",
        severity=sev,
        slug=slug,
        data={
            "from_state": from_state,
            "to_state": to_state,
            "actor": actor,
            "reason": reason,
            "rejection_category": rejection_category,
        },
    )


def history(slug: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT from_state, to_state, reason, actor, timestamp "
            "FROM lifecycle_transitions WHERE slug=? ORDER BY id ASC",
            (slug,),
        ).fetchall()
    return [dict(r) for r in rows]


def record_experiment(
    slug: str,
    params: dict[str, Any],
    data_start: str,
    data_end: str,
    sharpe: float,
    max_dd: float,
    fill_rate: float,
    pnl: float,
    n_trades: int,
    code_hash: str = "",
    data_hash: str = "",
    walk_forward_oos_sharpe: float | None = None,
    kill_switch_triggered: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    now = datetime.now(tz=UTC).isoformat()
    with _open(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO experiments "
            "(slug, params_json, data_start, data_end, sharpe, max_dd, "
            " fill_rate, pnl, n_trades, walk_forward_oos_sharpe, "
            " code_hash, data_hash, kill_switch_triggered, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                slug, json.dumps(params), data_start, data_end,
                sharpe, max_dd, fill_rate, pnl, n_trades,
                walk_forward_oos_sharpe, code_hash, data_hash,
                1 if kill_switch_triggered else 0, now,
            ),
        )
        return int(cur.lastrowid or 0)


def list_experiments(
    slug: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with _open(db_path) as conn:
        sql = "SELECT * FROM experiments WHERE 1=1"
        params: list[Any] = []
        if slug:
            sql += " AND slug=?"
            params.append(slug)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _row_to_hypothesis(row: sqlite3.Row) -> Hypothesis:
    try:
        criteria = json.loads(row["market_criteria_json"] or "{}")
    except Exception:
        criteria = {}
    return Hypothesis(
        slug=row["slug"],
        state=row["state"],
        source_url=row["source_url"] or "",
        source_type=row["source_type"] or "manual",
        summary=row["summary"] or "",
        rejection_reason=row["rejection_reason"],
        rejection_category=row["rejection_category"],
        parent_slug=row["parent_slug"],
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        market_criteria=criteria,
        strategy_module=row["strategy_module"] or "",
        strategy_class=row["strategy_class"] or "",
        strategy_config_class=row["strategy_config_class"] or "",
    )
