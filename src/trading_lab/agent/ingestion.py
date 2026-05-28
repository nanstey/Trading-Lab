r"""
Ingestion queue — track upstream strategy lifecycle from raw capture to PROPOSED.

Lives alongside `lifecycle` in `research/experiments.db` but writes to two
separate tables: `ingestion_items` (one row per captured idea lineage) and
`ingestion_events` (append-only audit log).

Stage progression:
    CAPTURED -> DOSSIER_READY -> MEMO_READY -> SPEC_READY -> DISCOVERED
                                                          \-> REJECTED_SOURCE
                                                          \-> SHELVED_SOURCE

Discovery is the only path into the `hypotheses` table; this module never
writes there.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from trading_lab.agent.lifecycle import DEFAULT_DB_PATH, SCHEMA


class Stage(StrEnum):
    CAPTURED = "CAPTURED"
    DOSSIER_READY = "DOSSIER_READY"
    MEMO_READY = "MEMO_READY"
    SPEC_READY = "SPEC_READY"
    DISCOVERED = "DISCOVERED"
    REJECTED_SOURCE = "REJECTED_SOURCE"
    SHELVED_SOURCE = "SHELVED_SOURCE"


class Status(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


TERMINAL_STAGES = frozenset({Stage.DISCOVERED, Stage.REJECTED_SOURCE, Stage.SHELVED_SOURCE})


@dataclass
class IngestionItem:
    intake_id: int
    source_url: str
    source_type: str
    source_title: str
    capture_slug: str
    thesis_name: str
    thesis_slug: str
    folder_path: str
    raw_capture_path: str
    stage: str
    status: str
    next_action: str
    notes: str
    created_at: str
    updated_at: str


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


def _row_to_item(row: sqlite3.Row) -> IngestionItem:
    return IngestionItem(
        intake_id=int(row["intake_id"]),
        source_url=row["source_url"] or "",
        source_type=row["source_type"] or "",
        source_title=row["source_title"] or "",
        capture_slug=row["capture_slug"] or "",
        thesis_name=row["thesis_name"] or "",
        thesis_slug=row["thesis_slug"] or "",
        folder_path=row["folder_path"] or "",
        raw_capture_path=row["raw_capture_path"] or "",
        stage=row["stage"],
        status=row["status"],
        next_action=row["next_action"] or "",
        notes=row["notes"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def record_event(
    intake_id: int,
    action: str,
    *,
    actor: str,
    from_stage: str | None = None,
    to_stage: str | None = None,
    details: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    now = datetime.now(tz=UTC).isoformat()
    with _open(db_path) as conn:
        conn.execute(
            "INSERT INTO ingestion_events "
            "(intake_id, timestamp, actor, from_stage, to_stage, action, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                intake_id,
                now,
                actor,
                from_stage,
                to_stage,
                action,
                json.dumps(details or {}, sort_keys=True),
            ),
        )


def record_intake(
    *,
    source_url: str,
    source_type: str,
    source_title: str,
    capture_slug: str,
    folder_path: str | Path,
    raw_capture_path: str | Path | None = None,
    stage: str = Stage.CAPTURED.value,
    status: str = Status.PENDING.value,
    actor: str = "system",
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Insert a new ingestion row or return the existing intake_id for this source_url.

    Idempotent on `source_url`. If a row already exists, returns its intake_id
    without changing stage/status — callers should use `advance_stage` for that.
    """
    now = datetime.now(tz=UTC).isoformat()
    folder = str(folder_path)
    raw_path = str(raw_capture_path) if raw_capture_path else None

    with _open(db_path) as conn:
        existing = conn.execute(
            "SELECT intake_id FROM ingestion_items WHERE source_url=?", (source_url,)
        ).fetchone()
        if existing is not None:
            return int(existing["intake_id"])
        cur = conn.execute(
            "INSERT INTO ingestion_items "
            "(source_url, source_type, source_title, capture_slug, "
            " folder_path, raw_capture_path, stage, status, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_url,
                source_type,
                source_title,
                capture_slug,
                folder,
                raw_path,
                stage,
                status,
                now,
                now,
            ),
        )
        intake_id = int(cur.lastrowid or 0)
    record_event(
        intake_id,
        "intake_recorded",
        actor=actor,
        from_stage=None,
        to_stage=stage,
        details={"source_url": source_url, "capture_slug": capture_slug, "folder_path": folder},
        db_path=db_path,
    )
    return intake_id


def advance_stage(
    intake_id: int,
    to_stage: str,
    *,
    status: str = Status.PENDING.value,
    actor: str,
    action: str = "advance_stage",
    next_action: str | None = None,
    details: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Update stage/status and log an event. Refuses backward moves silently — just logs them."""
    now = datetime.now(tz=UTC).isoformat()
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT stage FROM ingestion_items WHERE intake_id=?", (intake_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown intake_id: {intake_id}")
        from_stage = row["stage"]
        conn.execute(
            "UPDATE ingestion_items SET stage=?, status=?, "
            " next_action=COALESCE(?, next_action), updated_at=? "
            "WHERE intake_id=?",
            (to_stage, status, next_action, now, intake_id),
        )
    record_event(
        intake_id,
        action,
        actor=actor,
        from_stage=from_stage,
        to_stage=to_stage,
        details=details,
        db_path=db_path,
    )


def set_thesis_identity(
    intake_id: int,
    *,
    thesis_name: str,
    thesis_slug: str,
    folder_path: str | Path,
    actor: str,
    codename: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Record the canonical strategy identity assigned at the naming checkpoint."""
    now = datetime.now(tz=UTC).isoformat()
    with _open(db_path) as conn:
        conn.execute(
            "UPDATE ingestion_items SET thesis_name=?, thesis_slug=?, "
            " folder_path=?, updated_at=? "
            "WHERE intake_id=?",
            (thesis_name, thesis_slug, str(folder_path), now, intake_id),
        )
    record_event(
        intake_id,
        "thesis_named",
        actor=actor,
        details={
            "thesis_name": thesis_name,
            "thesis_slug": thesis_slug,
            "codename": codename,
            "folder_path": str(folder_path),
        },
        db_path=db_path,
    )


def get(intake_id: int, db_path: Path = DEFAULT_DB_PATH) -> IngestionItem | None:
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ingestion_items WHERE intake_id=?", (intake_id,)
        ).fetchone()
    return _row_to_item(row) if row else None


def get_by_source_url(source_url: str, db_path: Path = DEFAULT_DB_PATH) -> IngestionItem | None:
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ingestion_items WHERE source_url=?", (source_url,)
        ).fetchone()
    return _row_to_item(row) if row else None


def get_by_slug(slug: str, db_path: Path = DEFAULT_DB_PATH) -> IngestionItem | None:
    """Match against thesis_slug first, then capture_slug."""
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ingestion_items WHERE thesis_slug=? OR capture_slug=? "
            "ORDER BY (thesis_slug = ?) DESC LIMIT 1",
            (slug, slug, slug),
        ).fetchone()
    return _row_to_item(row) if row else None


def list_items(
    *,
    stage: str | None = None,
    status: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[IngestionItem]:
    sql = "SELECT * FROM ingestion_items WHERE 1=1"
    params: list[Any] = []
    if stage:
        sql += " AND stage=?"
        params.append(stage)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY created_at ASC"
    with _open(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_item(r) for r in rows]


def next_pending(stage: str, db_path: Path = DEFAULT_DB_PATH) -> IngestionItem | None:
    """Oldest PENDING item at the given stage. Deterministic queue selector for crons."""
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ingestion_items WHERE stage=? AND status=? "
            "ORDER BY created_at ASC LIMIT 1",
            (stage, Status.PENDING.value),
        ).fetchone()
    return _row_to_item(row) if row else None


def history(intake_id: int, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT timestamp, actor, from_stage, to_stage, action, details_json "
            "FROM ingestion_events WHERE intake_id=? ORDER BY id ASC",
            (intake_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            details = json.loads(r["details_json"] or "{}")
        except Exception:
            details = {}
        out.append(
            {
                "timestamp": r["timestamp"],
                "actor": r["actor"],
                "from_stage": r["from_stage"],
                "to_stage": r["to_stage"],
                "action": r["action"],
                "details": details,
            }
        )
    return out
