"""
Hyperliquid top-N universe selection.

Two concerns kept separate:

  * `compute_universe(...)` — query HL for the live universe and rank coins
    by notional volume. Used by `scripts/refresh_hl_universe.py` to write
    monthly point-in-time snapshots.

  * `load_universe(...)` — read back the snapshot that was in force at a
    given date. Backtests should always go through this so they use the
    universe that **was** top-N at the test window, not today's. Prevents
    forward-looking / survivorship bias.

Volume methodology:
  - Default `method="live_24h"` uses HL's `metaAndAssetCtxs.dayNtlVlm` (current
    rolling 24h notional). Cheap, no historical dependency.
  - `method="historical_30d"` aggregates `volume * close` from stored 1d
    candles. Only available once enough history is in the catalog; fall back
    to `live_24h` automatically when coverage is thin.

Tier labels (fixed for a top-20 universe):
    rank  1..5  -> tier_1   (mega-cap)
    rank  6..10 -> tier_2   (large-cap)
    rank 11..20 -> tier_3   (mid-cap)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.venues.hyperliquid.historical import (
    HyperliquidHistoricalClient,
)


def tier_for_rank(rank: int) -> str:
    if 1 <= rank <= 5:
        return "tier_1"
    if 6 <= rank <= 10:
        return "tier_2"
    if 11 <= rank <= 20:
        return "tier_3"
    return "tier_other"


@dataclass(frozen=True)
class UniverseEntry:
    coin: str
    rank: int
    tier: str
    day_ntl_vlm: float
    open_interest: float
    mark_px: float
    funding: float
    sz_decimals: int
    max_leverage: int


async def compute_universe(
    client: HyperliquidHistoricalClient,
    *,
    top_n: int = 20,
    method: str = "live_24h",
    lookback_days: int = 30,
    catalog: HyperliquidCatalog | None = None,
    as_of: datetime | None = None,
    exclude_isolated: bool = True,
) -> list[UniverseEntry]:
    """
    Query HL and rank the live perp universe by notional volume.

    Parameters
    ----------
    method : "live_24h" | "historical_30d"
        Volume source for ranking. `historical_30d` requires the catalog to
        contain >= `lookback_days` of 1d candles for enough coins; falls back
        to `live_24h` if coverage is insufficient.
    exclude_isolated : bool
        When True, drop coins flagged `onlyIsolated=True` in HL meta (less
        liquid / margin-restricted listings).
    """
    meta, ctxs = await client.get_meta_and_asset_ctxs()
    universe = meta.get("universe", [])

    rows: list[dict[str, Any]] = []
    for asset, ctx in zip(universe, ctxs, strict=False):
        if not isinstance(asset, dict):
            continue
        if asset.get("isDelisted"):
            continue
        if exclude_isolated and asset.get("onlyIsolated"):
            continue
        coin = asset["name"]
        rows.append(
            {
                "coin": coin,
                "day_ntl_vlm": float(ctx.get("dayNtlVlm") or 0.0),
                "open_interest": float(ctx.get("openInterest") or 0.0),
                "mark_px": float(ctx.get("markPx") or 0.0),
                "funding": float(ctx.get("funding") or 0.0),
                "sz_decimals": int(asset.get("szDecimals") or 0),
                "max_leverage": int(asset.get("maxLeverage") or 0),
            }
        )

    if not rows:
        raise RuntimeError("HL returned no universe entries — refusing to write empty snapshot.")

    df = pd.DataFrame(rows)

    if method == "historical_30d" and catalog is not None:
        df["rank_volume"] = df["coin"].apply(
            lambda c: _historical_notional(catalog, c, lookback_days, as_of)
        )
        # If we couldn't get a positive 30d figure for at least top_n coins,
        # fall back to live 24h volume.
        if (df["rank_volume"] > 0).sum() < top_n:
            df["rank_volume"] = df["day_ntl_vlm"]
            method = "live_24h_fallback"
    else:
        df["rank_volume"] = df["day_ntl_vlm"]

    df = df.sort_values("rank_volume", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    df["tier"] = df["rank"].map(tier_for_rank)

    top = df.head(top_n)
    return [
        UniverseEntry(
            coin=r.coin,
            rank=int(r.rank),
            tier=r.tier,
            day_ntl_vlm=float(r.day_ntl_vlm),
            open_interest=float(r.open_interest),
            mark_px=float(r.mark_px),
            funding=float(r.funding),
            sz_decimals=int(r.sz_decimals),
            max_leverage=int(r.max_leverage),
        )
        for r in top.itertuples()
    ]


def _historical_notional(
    catalog: HyperliquidCatalog,
    coin: str,
    lookback_days: int,
    as_of: datetime | None,
) -> float:
    end = as_of or datetime.now(UTC)
    start = end - timedelta(days=lookback_days)
    df = catalog.read_candles(coin, "1d", start, end)
    if df.empty:
        return 0.0
    # `volume` from HL candles is in base-asset units; multiply by close
    # for a USD notional estimate. Good enough for ranking.
    return float((df["volume"] * df["close"]).sum())


# ---------------------------------------------------------------------------
# Reading back point-in-time snapshots
# ---------------------------------------------------------------------------


def load_universe(catalog: HyperliquidCatalog, as_of: datetime) -> list[UniverseEntry]:
    """
    Return the universe snapshot that was in force on or before `as_of`.

    If no snapshot exists yet, returns []. Callers should treat empty as
    "no universe data — bail rather than guess."
    """
    snapshots = catalog.list_universe_snapshots()
    if not snapshots:
        return []
    target = as_of.date().isoformat()
    chosen: str | None = None
    for s in snapshots:
        if s <= target:
            chosen = s
        else:
            break
    if chosen is None:
        # Earliest snapshot is still after as_of — fall back to earliest.
        chosen = snapshots[0]
    df = catalog.read_universe_snapshot(chosen)
    return [
        UniverseEntry(
            coin=str(r.coin),
            rank=int(r.rank),
            tier=str(r.tier),
            day_ntl_vlm=float(r.day_ntl_vlm),
            open_interest=float(r.open_interest),
            mark_px=float(r.mark_px),
            funding=float(r.funding),
            sz_decimals=int(r.sz_decimals),
            max_leverage=int(r.max_leverage),
        )
        for r in df.itertuples()
    ]


def filter_by_tier(
    universe: list[UniverseEntry], tiers: list[str] | None
) -> list[UniverseEntry]:
    """Return only entries whose tier is in `tiers` (None = no filter)."""
    if not tiers:
        return list(universe)
    keep = set(tiers)
    return [u for u in universe if u.tier in keep]


def universe_as_dicts(universe: list[UniverseEntry]) -> list[dict[str, Any]]:
    """Lightweight serializer for the catalog snapshot writer / JSON output."""
    return [
        {
            "coin": u.coin,
            "rank": u.rank,
            "tier": u.tier,
            "day_ntl_vlm": u.day_ntl_vlm,
            "open_interest": u.open_interest,
            "mark_px": u.mark_px,
            "funding": u.funding,
            "sz_decimals": u.sz_decimals,
            "max_leverage": u.max_leverage,
        }
        for u in universe
    ]


# Convenience for scripts that just want a sorted list of coin names.
def coin_names(universe: list[UniverseEntry]) -> list[str]:
    return [u.coin for u in universe]


# Optional: write a snapshot through the catalog in one call.
def persist_snapshot(
    catalog: HyperliquidCatalog,
    snapshot_date: str,
    universe: list[UniverseEntry],
) -> Path:
    rows = universe_as_dicts(universe)
    catalog.write_universe_snapshot(snapshot_date, rows)
    return catalog._universe_dir / f"{snapshot_date}.parquet"  # noqa: SLF001
