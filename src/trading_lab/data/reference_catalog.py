"""
Reference-price Parquet catalog for external market data.

Stores timestamped price ticks for external feeds used to research Polymarket
microstructure strategies (for example RTDS Binance / Chainlink crypto price
streams). Layout:

    data/parquet/reference_prices/
        <source>/<symbol>/<YYYY>/<MM>/data.parquet

Writers are idempotent per partition: overlapping re-captures merge with the
existing partition and deduplicate on `ts_ms` (last-write wins).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

REFERENCE_TICKS_SCHEMA = pa.schema(
    [
        pa.field("source", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("ts_ms", pa.int64()),
        pa.field("value", pa.float64()),
    ]
)


class ReferencePriceCatalog:
    """Read/write Parquet store for external reference price ticks."""

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "reference_prices"
        self._root.mkdir(parents=True, exist_ok=True)

    def write_ticks(self, source: str, symbol: str, ticks: list[dict[str, Any]]) -> int:
        if not ticks:
            return 0
        source = _clean(source)
        symbol = _clean(symbol)
        df = pd.DataFrame.from_records(
            [
                {
                    "source": source,
                    "symbol": symbol,
                    "ts_ms": int(t["ts_ms"]),
                    "value": float(t["value"]),
                }
                for t in ticks
                if t.get("ts_ms") is not None and t.get("value") is not None
            ]
        )
        if df.empty:
            return 0
        df["dt"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)

        rows_written = 0
        for (year, month), part in df.groupby([df["dt"].dt.year, df["dt"].dt.month]):
            path = self._partition_path(source, symbol, int(year), int(month))
            merged = _merge_partition(path, part.drop(columns=["dt"]), key="ts_ms")
            table = pa.Table.from_pandas(merged, schema=REFERENCE_TICKS_SCHEMA, preserve_index=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path, compression="snappy")
            rows_written += len(merged)
        return rows_written

    def read_ticks(self, source: str, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        source = _clean(source)
        symbol = _clean(symbol)
        root = self._root / source / symbol
        if not root.exists():
            return _empty_ticks_df()
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        frames: list[pd.DataFrame] = []
        for pf in sorted(root.rglob("data.parquet")):
            df = pq.read_table(pf).to_pandas()
            df = df[(df["ts_ms"] >= start_ms) & (df["ts_ms"] <= end_ms)]
            if not df.empty:
                frames.append(df)
        if not frames:
            return _empty_ticks_df()
        out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_ms"])
        out = out.sort_values("ts_ms")
        out["dt"] = pd.to_datetime(out["ts_ms"], unit="ms", utc=True)
        return out.reset_index(drop=True)

    def ticks_summary(self, source: str, symbol: str) -> dict[str, Any]:
        source = _clean(source)
        symbol = _clean(symbol)
        root = self._root / source / symbol
        if not root.exists():
            return {"source": source, "symbol": symbol, "rows": 0}
        rows = 0
        min_ms: int | None = None
        max_ms: int | None = None
        for pf in sorted(root.rglob("data.parquet")):
            table = pq.read_table(pf, columns=["ts_ms"])
            if table.num_rows == 0:
                continue
            rows += table.num_rows
            col = table.column("ts_ms").to_pylist()
            local_min, local_max = min(col), max(col)
            min_ms = local_min if min_ms is None else min(min_ms, local_min)
            max_ms = local_max if max_ms is None else max(max_ms, local_max)
        return {
            "source": source,
            "symbol": symbol,
            "rows": rows,
            "first_ts_iso": _iso(min_ms),
            "last_ts_iso": _iso(max_ms),
        }

    def _partition_path(self, source: str, symbol: str, year: int, month: int) -> Path:
        return self._root / source / symbol / f"{year:04d}" / f"{month:02d}" / "data.parquet"


def _merge_partition(path: Path, incoming: pd.DataFrame, key: str) -> pd.DataFrame:
    if path.exists():
        existing = pq.read_table(path).to_pandas()
        merged = pd.concat([existing, incoming], ignore_index=True)
    else:
        merged = incoming.copy()
    merged = merged.sort_values(key).drop_duplicates(subset=[key], keep="last")
    return merged.reset_index(drop=True)


def _empty_ticks_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["source", "symbol", "ts_ms", "value", "dt"])


def _clean(value: str) -> str:
    return str(value).strip().lower().replace("/", "_")


def _iso(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat()
