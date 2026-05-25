"""
MarketCatalog — sqlite store for Polymarket market metadata.

Separate from `DataCatalog` (Parquet time-series) because metadata is
schema-stable, refreshed daily, and queried by criteria — different access
pattern than time-series. Backs `select_markets()` in `data/market_filter.py`.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    question TEXT,
    category TEXT,
    event_slug TEXT,
    event_title TEXT,
    series_slug TEXT,
    outcome_type TEXT,
    yes_token_id TEXT,
    no_token_id TEXT,
    volume_usdc REAL,
    volume_24h_usdc REAL,
    liquidity_usdc REAL,
    active INTEGER,
    archived INTEGER,
    closed INTEGER,
    start_date_iso TEXT,
    end_date_iso TEXT,
    resolved_outcome TEXT,
    resolved_at TEXT,
    tick_size REAL,
    min_order_size REAL,
    tags_json TEXT,
    raw_json TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_markets_active   ON markets(active, archived, closed);
CREATE INDEX IF NOT EXISTS idx_markets_series   ON markets(series_slug);
CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_volume   ON markets(volume_24h_usdc DESC);
"""


@dataclass(frozen=True)
class MarketRow:
    condition_id: str
    question: str
    category: str
    event_slug: str
    event_title: str
    series_slug: str | None
    outcome_type: str
    yes_token_id: str
    no_token_id: str
    volume_usdc: float
    volume_24h_usdc: float
    liquidity_usdc: float
    active: bool
    archived: bool
    closed: bool
    start_date_iso: str
    end_date_iso: str
    resolved_outcome: str | None
    resolved_at: str | None
    tick_size: float
    min_order_size: float
    tags_json: str
    fetched_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row | tuple) -> MarketRow:
        keys = ["condition_id", "question", "category", "event_slug", "event_title", "series_slug", "outcome_type", "yes_token_id", "no_token_id", "volume_usdc", "volume_24h_usdc", "liquidity_usdc", "active", "archived", "closed", "start_date_iso", "end_date_iso", "resolved_outcome", "resolved_at", "tick_size", "min_order_size", "tags_json", "fetched_at"]
        if isinstance(row, sqlite3.Row):
            data = {k: row[k] for k in keys}
        else:
            data = dict(zip(keys, row, strict=False))
        return cls(
            condition_id=str(data["condition_id"] or ""),
            question=str(data["question"] or ""),
            category=str(data["category"] or ""),
            event_slug=str(data["event_slug"] or ""),
            event_title=str(data["event_title"] or ""),
            series_slug=data["series_slug"],
            outcome_type=str(data["outcome_type"] or "binary"),
            yes_token_id=str(data["yes_token_id"] or ""),
            no_token_id=str(data["no_token_id"] or ""),
            volume_usdc=float(data["volume_usdc"] or 0),
            volume_24h_usdc=float(data["volume_24h_usdc"] or 0),
            liquidity_usdc=float(data["liquidity_usdc"] or 0),
            active=bool(data["active"]),
            archived=bool(data["archived"]),
            closed=bool(data["closed"]),
            start_date_iso=str(data["start_date_iso"] or ""),
            end_date_iso=str(data["end_date_iso"] or ""),
            resolved_outcome=data["resolved_outcome"],
            resolved_at=data["resolved_at"],
            tick_size=float(data["tick_size"] or 0.01),
            min_order_size=float(data["min_order_size"] or 5.0),
            tags_json=str(data["tags_json"] or "[]"),
            fetched_at=str(data["fetched_at"] or ""),
        )


def _strip_date_suffix(slug: str) -> str:
    """
    Heuristic: drop a trailing date/numeric suffix from an event slug.

    Examples
    --------
    btc-updown-5m-1779679800  -> btc-updown-5m
    btc-updown-4h-1779667200  -> btc-updown-4h
    foo-bar-2026-05-25        -> foo-bar
    foo-bar                   -> foo-bar
    """
    return re.sub(r"-\d{4,}(-\d{2,}){0,2}$", "", slug)


def _outcome_type_from_market(m: dict[str, Any]) -> str:
    outcomes_raw = m.get("outcomes") or "[]"
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    except Exception:
        outcomes = []
    if isinstance(outcomes, list) and len(outcomes) == 2:
        names = {str(o).strip().lower() for o in outcomes}
        if names == {"yes", "no"}:
            return "binary"
        return "multi"
    if isinstance(outcomes, list):
        if len(outcomes) > 2:
            return "multi"
        return "scalar"
    return "binary"


def _split_token_ids(m: dict[str, Any]) -> tuple[str, str]:
    raw = m.get("clobTokenIds") or "[]"
    try:
        ids = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        ids = []
    if isinstance(ids, list) and len(ids) >= 2:
        return str(ids[0]), str(ids[1])
    return "", ""


def gamma_to_row(m: dict[str, Any], fetched_at: str, event_slug: str = "") -> dict[str, Any]:
    """Map a gamma `/markets` response row to our DB schema."""
    yes_id, no_id = _split_token_ids(m)
    outcome_type = _outcome_type_from_market(m)
    # Series derivation: strip trailing date/numeric tokens from event slug.
    derived_series = _strip_date_suffix(event_slug) if event_slug else None
    series_slug = derived_series if derived_series and derived_series != event_slug else None

    return {
        "condition_id": str(m.get("conditionId") or ""),
        "question": str(m.get("question") or ""),
        "category": str(m.get("category") or ""),
        "event_slug": event_slug,
        "event_title": str(m.get("groupItemTitle") or ""),
        "series_slug": series_slug,
        "outcome_type": outcome_type,
        "yes_token_id": yes_id,
        "no_token_id": no_id,
        "volume_usdc": float(m.get("volumeNum") or 0),
        "volume_24h_usdc": float(m.get("volume24hr") or 0),
        "liquidity_usdc": float(m.get("liquidityNum") or 0),
        "active": 1 if m.get("active") else 0,
        "archived": 1 if m.get("archived") else 0,
        "closed": 1 if m.get("closed") else 0,
        "start_date_iso": str(m.get("startDateIso") or m.get("startDate") or ""),
        "end_date_iso": str(m.get("endDateIso") or m.get("endDate") or ""),
        "resolved_outcome": _resolved_outcome(m),
        "resolved_at": str(m.get("resolvedAt") or "") or None,
        "tick_size": float(m.get("orderPriceMinTickSize") or 0.01),
        "min_order_size": float(m.get("orderMinSize") or 5.0),
        "tags_json": json.dumps(m.get("tags") or []),
        "raw_json": json.dumps(m),
        "fetched_at": fetched_at,
    }


def _resolved_outcome(m: dict[str, Any]) -> str | None:
    if not m.get("closed"):
        return None
    raw = m.get("outcomePrices") or "[]"
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if isinstance(prices, list) and len(prices) >= 2:
        try:
            y, n = float(prices[0]), float(prices[1])
        except (TypeError, ValueError):
            return None
        if y > 0.99 and n < 0.01:
            return "YES"
        if n > 0.99 and y < 0.01:
            return "NO"
    return None


class MarketCatalog:
    """SQLite-backed metadata store for Polymarket markets."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_market(self, row: dict[str, Any]) -> None:
        keys = list(row.keys())
        placeholders = ",".join(["?"] * len(keys))
        cols = ",".join(keys)
        updates = ",".join(f"{k}=excluded.{k}" for k in keys if k != "condition_id")
        sql = (
            f"INSERT INTO markets ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(condition_id) DO UPDATE SET {updates}"
        )
        self._conn.execute(sql, [row[k] for k in keys])
        self._conn.commit()

    def upsert_many(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        keys = list(rows[0].keys())
        placeholders = ",".join(["?"] * len(keys))
        cols = ",".join(keys)
        updates = ",".join(f"{k}=excluded.{k}" for k in keys if k != "condition_id")
        sql = (
            f"INSERT INTO markets ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(condition_id) DO UPDATE SET {updates}"
        )
        with self._conn:
            self._conn.executemany(sql, [[r[k] for k in keys] for r in rows])
        return len(rows)

    def get_market(self, condition_id: str) -> MarketRow | None:
        cur = self._conn.execute(
            "SELECT * FROM markets WHERE condition_id=?", (condition_id,)
        )
        row = cur.fetchone()
        return MarketRow.from_row(row) if row else None

    def query(
        self,
        where_clause: str = "1=1",
        params: list[Any] | None = None,
        order_by: str = "volume_24h_usdc DESC",
        limit: int = 50,
    ) -> list[MarketRow]:
        sql = f"SELECT * FROM markets WHERE {where_clause} ORDER BY {order_by} LIMIT ?"
        full_params = (params or []) + [int(limit)]
        cur = self._conn.execute(sql, full_params)
        return [MarketRow.from_row(r) for r in cur.fetchall()]

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM markets")
        return int(cur.fetchone()[0])
