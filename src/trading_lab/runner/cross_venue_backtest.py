from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from trading_lab.data.catalog import DataCatalog
from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.research.cross_venue import CrossVenueSpec, load_cross_venue_spec, validate_cross_venue_spec


@dataclass(frozen=True)
class CrossVenueTimelineEvent:
    ts_ns: int
    source: str
    instrument: str
    event_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_ns": self.ts_ns,
            "ts_iso": _ns_to_iso(self.ts_ns),
            "source": self.source,
            "instrument": self.instrument,
            "event_type": self.event_type,
        }


@dataclass(frozen=True)
class CrossVenueBacktestPlan:
    slug: str
    spec: CrossVenueSpec
    start: datetime
    end: datetime
    hl_interval: str
    timeline: list[CrossVenueTimelineEvent]
    timeline_counts: dict[str, int]
    alignment: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "slug": self.slug,
            "spec": self.spec.to_dict(),
            "window": {
                "start_iso": self.start.isoformat(),
                "end_iso": self.end.isoformat(),
                "hl_interval": self.hl_interval,
            },
            "timeline_counts": dict(self.timeline_counts),
            "alignment": dict(self.alignment),
            "timeline_preview": [e.to_dict() for e in self.timeline[:10]],
        }


class CrossVenueBacktestRunner:
    def __init__(
        self,
        *,
        data_dir: Path = Path("data/parquet"),
        hl_interval: str = "1h",
    ) -> None:
        self._data_dir = Path(data_dir)
        self._hl_interval = hl_interval
        self._pm = DataCatalog(self._data_dir)
        self._hl = HyperliquidCatalog(self._data_dir)

    def build_plan(
        self,
        *,
        spec: CrossVenueSpec,
        start: datetime,
        end: datetime,
    ) -> CrossVenueBacktestPlan:
        yes_df = self._pm.read_trades(spec.polymarket.yes_token_id, start, end)
        no_df = self._pm.read_trades(spec.polymarket.no_token_id, start, end)
        hl_df = self._hl.read_candles(spec.hyperliquid.symbol or "", self._hl_interval, start, end)

        timeline = self._build_timeline(spec, yes_df=yes_df, no_df=no_df, hl_df=hl_df)
        counts = {
            "polymarket_yes_trades": int(len(yes_df)),
            "polymarket_no_trades": int(len(no_df)),
            "hyperliquid_bars": int(len(hl_df)),
            "unified_events": int(len(timeline)),
        }
        alignment = self._build_alignment(yes_df=yes_df, no_df=no_df, hl_df=hl_df)
        return CrossVenueBacktestPlan(
            slug=spec.slug,
            spec=spec,
            start=start,
            end=end,
            hl_interval=self._hl_interval,
            timeline=timeline,
            timeline_counts=counts,
            alignment=alignment,
        )

    def _build_timeline(
        self,
        spec: CrossVenueSpec,
        *,
        yes_df: pd.DataFrame,
        no_df: pd.DataFrame,
        hl_df: pd.DataFrame,
    ) -> list[CrossVenueTimelineEvent]:
        events: list[CrossVenueTimelineEvent] = []
        for row in yes_df.itertuples(index=False):
            events.append(
                CrossVenueTimelineEvent(
                    ts_ns=int(row.timestamp) * 1_000_000,
                    source="polymarket_yes",
                    instrument=spec.polymarket.yes_token_id,
                    event_type="trade",
                )
            )
        for row in no_df.itertuples(index=False):
            events.append(
                CrossVenueTimelineEvent(
                    ts_ns=int(row.timestamp) * 1_000_000,
                    source="polymarket_no",
                    instrument=spec.polymarket.no_token_id,
                    event_type="trade",
                )
            )
        for row in hl_df.itertuples(index=False):
            events.append(
                CrossVenueTimelineEvent(
                    ts_ns=int(row.ts_close_ms) * 1_000_000,
                    source="hyperliquid",
                    instrument=spec.hyperliquid.symbol or "",
                    event_type="bar",
                )
            )
        return sorted(events, key=lambda e: (e.ts_ns, e.source, e.instrument))

    def _build_alignment(self, *, yes_df: pd.DataFrame, no_df: pd.DataFrame, hl_df: pd.DataFrame) -> dict[str, Any]:
        gaps: list[str] = []
        yes_cov = _trade_coverage(yes_df)
        no_cov = _trade_coverage(no_df)
        hl_cov = _bar_coverage(hl_df)
        pm_cov = _merge_coverages(yes_cov, no_cov)
        coverage: dict[str, dict[str, Any]] = {
            "polymarket_yes": yes_cov,
            "polymarket_no": no_cov,
            "polymarket_combined": pm_cov,
            "hyperliquid": hl_cov,
        }
        if yes_df.empty:
            gaps.append("missing_polymarket_yes_trades")
        if no_df.empty:
            gaps.append("missing_polymarket_no_trades")
        if hl_df.empty:
            gaps.append("missing_hyperliquid_bars")

        starts = [c["first_ts_ns"] for c in (pm_cov, hl_cov) if c["first_ts_ns"] is not None]
        ends = [c["last_ts_ns"] for c in (pm_cov, hl_cov) if c["last_ts_ns"] is not None]
        overlap_start_ns = max(starts) if starts else None
        overlap_end_ns = min(ends) if ends else None
        overlap_ok = bool(
            overlap_start_ns is not None
            and overlap_end_ns is not None
            and overlap_start_ns <= overlap_end_ns
            and not {"missing_hyperliquid_bars"}.intersection(gaps)
            and pm_cov["count"] > 0
        )
        if starts and ends and not overlap_ok and "no_cross_venue_overlap_window" not in gaps:
            gaps.append("no_cross_venue_overlap_window")

        return {
            "overlap_ok": overlap_ok,
            "overlap_start_ns": overlap_start_ns,
            "overlap_end_ns": overlap_end_ns,
            "overlap_start_iso": _ns_to_iso(overlap_start_ns) if overlap_start_ns is not None else None,
            "overlap_end_iso": _ns_to_iso(overlap_end_ns) if overlap_end_ns is not None else None,
            "coverage": coverage,
            "gaps": gaps,
        }



def build_cross_venue_backtest_report(
    path: str | Path,
    *,
    start: datetime,
    end: datetime,
    data_dir: Path = Path("data/parquet"),
    hl_interval: str = "1h",
) -> dict[str, Any]:
    spec = load_cross_venue_spec(path)
    errors = validate_cross_venue_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    plan = CrossVenueBacktestRunner(data_dir=data_dir, hl_interval=hl_interval).build_plan(
        spec=spec,
        start=start,
        end=end,
    )
    return plan.to_dict()



def _trade_coverage(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"count": 0, "first_ts_ns": None, "last_ts_ns": None}
    first = int(df["timestamp"].min()) * 1_000_000
    last = int(df["timestamp"].max()) * 1_000_000
    return {
        "count": int(len(df)),
        "first_ts_ns": first,
        "last_ts_ns": last,
        "first_ts_iso": _ns_to_iso(first),
        "last_ts_iso": _ns_to_iso(last),
    }



def _bar_coverage(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"count": 0, "first_ts_ns": None, "last_ts_ns": None}
    first = int(df["ts_close_ms"].min()) * 1_000_000
    last = int(df["ts_close_ms"].max()) * 1_000_000
    return {
        "count": int(len(df)),
        "first_ts_ns": first,
        "last_ts_ns": last,
        "first_ts_iso": _ns_to_iso(first),
        "last_ts_iso": _ns_to_iso(last),
    }



def _merge_coverages(*coverages: dict[str, Any]) -> dict[str, Any]:
    non_empty = [c for c in coverages if c.get("count", 0) > 0 and c.get("first_ts_ns") is not None and c.get("last_ts_ns") is not None]
    if not non_empty:
        return {"count": 0, "first_ts_ns": None, "last_ts_ns": None}
    first = min(int(c["first_ts_ns"]) for c in non_empty)
    last = max(int(c["last_ts_ns"]) for c in non_empty)
    return {
        "count": int(sum(int(c["count"]) for c in non_empty)),
        "first_ts_ns": first,
        "last_ts_ns": last,
        "first_ts_iso": _ns_to_iso(first),
        "last_ts_iso": _ns_to_iso(last),
    }



def _ns_to_iso(ts_ns: int | None) -> str | None:
    if ts_ns is None:
        return None
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC).isoformat()
