"""Helpers for recurring Polymarket 5-minute crypto Up/Down markets."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ASSET_NAME_TO_CODE = {
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "XRP": "XRP",
}

_CODE_TO_DISPLAY = {v: k.title() for k, v in _ASSET_NAME_TO_CODE.items()}
_CODE_TO_RTD_SYMBOLS = {
    "BTC": {"binance": "btcusdt", "chainlink": "btc/usd"},
    "ETH": {"binance": "ethusdt", "chainlink": "eth/usd"},
    "SOL": {"binance": "solusdt", "chainlink": "sol/usd"},
    "XRP": {"binance": "xrpusdt", "chainlink": "xrp/usd"},
}

_UPDOWN_RE = re.compile(r"^(?P<asset>[A-Za-z]+) Up or Down - ")


@dataclass(frozen=True)
class PM5mMarket:
    asset: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    question: str
    market_slug: str
    event_start_iso: str
    event_end_iso: str
    active: bool
    closed: bool
    archived: bool
    volume_24h_usdc: float
    liquidity_usdc: float
    fees_enabled: bool
    description: str
    chainlink_symbol: str
    binance_symbol: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_polymarket_5m_markets(
    db_path: Path,
    *,
    assets: list[str] | None = None,
    active_only: bool = False,
    include_closed: bool = True,
    limit: int | None = None,
) -> list[PM5mMarket]:
    wanted = {a.strip().upper() for a in assets or [] if a.strip()}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT condition_id, question, yes_token_id, no_token_id,
                   volume_24h_usdc, liquidity_usdc, active, archived, closed,
                   raw_json
            FROM markets
            WHERE question LIKE '%Up or Down - %'
            ORDER BY active DESC, volume_24h_usdc DESC, liquidity_usdc DESC
            """
        ).fetchall()
    finally:
        conn.close()

    markets: list[PM5mMarket] = []
    for row in rows:
        raw = _loads_json(row["raw_json"])
        market = _row_to_market(row, raw)
        if market is None:
            continue
        if wanted and market.asset not in wanted:
            continue
        if active_only and not market.active:
            continue
        if not include_closed and market.closed:
            continue
        markets.append(market)
        if limit is not None and len(markets) >= limit:
            break
    return markets


def find_market_by_condition_id(db_path: Path, condition_id: str) -> PM5mMarket | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT condition_id, question, yes_token_id, no_token_id,
                   volume_24h_usdc, liquidity_usdc, active, archived, closed,
                   raw_json
            FROM markets
            WHERE condition_id = ?
            """,
            (condition_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_market(row, _loads_json(row["raw_json"]))


def _row_to_market(row: sqlite3.Row, raw: dict[str, Any]) -> PM5mMarket | None:
    question = str(row["question"] or "")
    asset = _asset_code_from_question(question)
    if asset is None:
        return None
    if asset not in _CODE_TO_RTD_SYMBOLS:
        return None
    market_slug = str(raw.get("slug") or "")
    if market_slug and "updown-5m" not in market_slug:
        return None
    start_dt = _parse_iso(raw.get("eventStartTime"))
    end_dt = _parse_iso(raw.get("endDate"))
    if start_dt is None or end_dt is None:
        return None
    duration_s = int((end_dt - start_dt).total_seconds())
    if duration_s < 240 or duration_s > 360:
        return None
    return PM5mMarket(
        asset=asset,
        condition_id=str(raw.get("conditionId") or row["condition_id"] or ""),
        yes_token_id=str(row["yes_token_id"] or ""),
        no_token_id=str(row["no_token_id"] or ""),
        question=question,
        market_slug=market_slug,
        event_start_iso=start_dt.isoformat().replace("+00:00", "Z"),
        event_end_iso=end_dt.isoformat().replace("+00:00", "Z"),
        active=bool(row["active"]),
        closed=bool(row["closed"]),
        archived=bool(row["archived"]),
        volume_24h_usdc=float(row["volume_24h_usdc"] or 0.0),
        liquidity_usdc=float(row["liquidity_usdc"] or 0.0),
        fees_enabled=bool(raw.get("feesEnabled")),
        description=str(raw.get("description") or ""),
        chainlink_symbol=_CODE_TO_RTD_SYMBOLS[asset]["chainlink"],
        binance_symbol=_CODE_TO_RTD_SYMBOLS[asset]["binance"],
    )


def _asset_code_from_question(question: str) -> str | None:
    m = _UPDOWN_RE.match(question.strip())
    if not m:
        return None
    return _ASSET_NAME_TO_CODE.get(m.group("asset").upper())


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _loads_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}
