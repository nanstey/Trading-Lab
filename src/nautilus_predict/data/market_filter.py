"""
MarketCriteria + select_markets — strategy-aware market selection.

Hypotheses declare what kind of markets they want to be tested on (binary,
high-volume, recurring series, etc.). `select_markets()` translates those
criteria into SQL against `MarketCatalog` and returns a ranked list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from nautilus_predict.data.market_catalog import MarketCatalog, MarketRow


@dataclass(frozen=True)
class MarketCriteria:
    outcome_type: str = "binary"
    min_volume_24h_usdc: float = 0.0
    min_liquidity_usdc: float = 0.0
    categories: list[str] | None = None
    tags_any: list[str] | None = None
    require_series: bool = False
    series_slug: str | None = None
    # (min_days_to_resolution, max_days_to_resolution)
    resolution_horizon_days: tuple[int, int] = (0, 9999)
    # None = any; True = resolved-only; False = active-only.
    resolved: bool | None = None
    count: int = 3
    sort_by: str = "volume_24h_usdc"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarketCriteria:
        defaults = cls()
        return cls(
            outcome_type=str(d.get("outcome_type", defaults.outcome_type)),
            min_volume_24h_usdc=float(d.get("min_volume_24h_usdc", 0)),
            min_liquidity_usdc=float(d.get("min_liquidity_usdc", 0)),
            categories=d.get("categories") or None,
            tags_any=d.get("tags_any") or None,
            require_series=bool(d.get("require_series", False)),
            series_slug=d.get("series_slug") or None,
            resolution_horizon_days=tuple(
                d.get("resolution_horizon_days", defaults.resolution_horizon_days)
            ),
            resolved=d.get("resolved"),
            count=int(d.get("count", defaults.count)),
            sort_by=str(d.get("sort_by", defaults.sort_by)),
        )


# Whitelist of columns that can appear in ORDER BY — prevents SQL injection
# via the hypothesis MD frontmatter.
_SORTABLE_COLS = {
    "volume_24h_usdc",
    "volume_usdc",
    "liquidity_usdc",
    "end_date_iso",
}


def select_markets(criteria: MarketCriteria, catalog: MarketCatalog) -> list[MarketRow]:
    """
    Apply `criteria` as SQL filters against `catalog`.

    Returns up to `criteria.count` markets ranked by `criteria.sort_by` DESC.
    """
    where: list[str] = []
    params: list[Any] = []

    where.append("outcome_type = ?")
    params.append(criteria.outcome_type)

    if criteria.min_volume_24h_usdc > 0:
        where.append("volume_24h_usdc >= ?")
        params.append(criteria.min_volume_24h_usdc)
    if criteria.min_liquidity_usdc > 0:
        where.append("liquidity_usdc >= ?")
        params.append(criteria.min_liquidity_usdc)

    if criteria.categories:
        placeholders = ",".join(["?"] * len(criteria.categories))
        where.append(f"category IN ({placeholders})")
        params.extend(criteria.categories)

    if criteria.require_series:
        where.append("series_slug IS NOT NULL")
    if criteria.series_slug:
        where.append("series_slug = ?")
        params.append(criteria.series_slug)

    if criteria.resolved is True:
        where.append("closed = 1 AND resolved_outcome IS NOT NULL")
    elif criteria.resolved is False:
        where.append("active = 1 AND closed = 0")

    # Resolution horizon — best effort against end_date_iso strings.
    min_days, max_days = criteria.resolution_horizon_days
    now = datetime.now(tz=UTC)
    if min_days > 0:
        where.append("end_date_iso >= ?")
        params.append((now + timedelta(days=min_days)).date().isoformat())
    if max_days < 9999:
        where.append("end_date_iso <= ?")
        params.append((now + timedelta(days=max_days)).date().isoformat())

    sort_col = criteria.sort_by if criteria.sort_by in _SORTABLE_COLS else "volume_24h_usdc"
    where_sql = " AND ".join(where) if where else "1=1"

    rows = catalog.query(
        where_clause=where_sql,
        params=params,
        order_by=f"{sort_col} DESC",
        limit=criteria.count * 4,  # over-fetch to allow tag post-filter
    )

    if criteria.tags_any:
        wanted = {t.lower() for t in criteria.tags_any}
        filtered = []
        for r in rows:
            try:
                tags = {str(t).lower() for t in json.loads(r.tags_json or "[]")}
            except Exception:
                tags = set()
            if tags & wanted:
                filtered.append(r)
        rows = filtered

    return rows[: criteria.count]
