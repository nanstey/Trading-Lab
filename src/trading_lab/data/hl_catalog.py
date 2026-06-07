"""
Hyperliquid Parquet catalog.

Separate tree from the Polymarket catalog in `catalog.py` to keep schemas
and partitioning independent (different cardinalities, different time
semantics). Layout:

    data/parquet/hyperliquid/
        candles/<COIN>/<INTERVAL>/<YYYY>/<MM>/data.parquet
        funding/<COIN>/<YYYY>/data.parquet
        universe_snapshots/<YYYY-MM-DD>.parquet

Plain directory names (not Hive `key=value`) are intentional: pyarrow's
dataset reader auto-injects Hive partition keys as columns, which would
conflict with the explicit `coin`/`interval` columns inside each file
and break `read_table()` on the parent directory.

Writers are idempotent: a re-run over the same window will replace the
overlapping partition file rather than appending duplicates. Readers
return pandas DataFrames keyed by UTC datetime, ready for the bar loader.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)


CANDLES_SCHEMA = pa.schema(
    [
        pa.field("coin", pa.string()),
        pa.field("interval", pa.string()),
        pa.field("ts_open_ms", pa.int64()),
        pa.field("ts_close_ms", pa.int64()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("n_trades", pa.int64()),
    ]
)

FUNDING_SCHEMA = pa.schema(
    [
        pa.field("coin", pa.string()),
        pa.field("ts_ms", pa.int64()),
        pa.field("funding_rate", pa.float64()),
        pa.field("premium", pa.float64()),
    ]
)

UNIVERSE_SNAPSHOT_SCHEMA = pa.schema(
    [
        pa.field("snapshot_date", pa.string()),  # YYYY-MM-DD
        pa.field("coin", pa.string()),
        pa.field("rank", pa.int32()),
        pa.field("tier", pa.string()),           # tier_1 / tier_2 / tier_3
        pa.field("day_ntl_vlm", pa.float64()),
        pa.field("open_interest", pa.float64()),
        pa.field("mark_px", pa.float64()),
        pa.field("funding", pa.float64()),
        pa.field("sz_decimals", pa.int32()),
        pa.field("max_leverage", pa.int32()),
    ]
)


class HyperliquidCatalog:
    """Read/write Parquet store for HL bars, funding, and universe snapshots."""

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "hyperliquid"
        self._candles_dir = self._root / "candles"
        self._funding_dir = self._root / "funding"
        self._universe_dir = self._root / "universe_snapshots"
        for d in (self._candles_dir, self._funding_dir, self._universe_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------

    def write_candles(
        self,
        coin: str,
        interval: str,
        candles: list[dict[str, Any]],
    ) -> int:
        """
        Persist a list of raw HL candle dicts.

        Partitions by (coin, interval, year, month) based on bar open-time.
        Within each partition file, existing rows are merged with new ones
        and deduplicated on `ts_open_ms` (last-write wins).
        Returns the total number of rows now stored across touched partitions.
        """
        if not candles:
            return 0
        records = [_candle_record(coin, interval, c) for c in candles]
        df = pd.DataFrame.from_records(records)
        df["dt"] = pd.to_datetime(df["ts_open_ms"], unit="ms", utc=True)

        rows_written = 0
        for (year, month), part in df.groupby([df["dt"].dt.year, df["dt"].dt.month]):
            path = self._candle_partition_path(coin, interval, int(year), int(month))
            merged = self._merge_partition(path, part.drop(columns=["dt"]), key="ts_open_ms")
            table = pa.Table.from_pandas(merged, schema=CANDLES_SCHEMA, preserve_index=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path, compression="snappy")
            rows_written += len(merged)
        return rows_written

    def read_candles(
        self,
        coin: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Return a pandas DataFrame of bars (UTC, sorted, deduped, indexed by open time)."""
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        coin_dir = self._candles_dir / coin / interval
        if not coin_dir.exists():
            fallback = self._read_resampled_candles(coin, interval, start, end)
            return fallback if fallback is not None else _empty_candle_df()

        frames: list[pd.DataFrame] = []
        for pf in sorted(coin_dir.rglob("data.parquet")):
            table = pq.read_table(pf)
            df = table.to_pandas()
            df = df[(df["ts_open_ms"] >= start_ms) & (df["ts_open_ms"] <= end_ms)]
            if not df.empty:
                frames.append(df)
        if not frames:
            fallback = self._read_resampled_candles(coin, interval, start, end)
            return fallback if fallback is not None else _empty_candle_df()
        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["ts_open_ms"]).sort_values("ts_open_ms")
        out["dt_open"] = pd.to_datetime(out["ts_open_ms"], unit="ms", utc=True)
        out["dt_close"] = pd.to_datetime(out["ts_close_ms"], unit="ms", utc=True)
        return out.reset_index(drop=True)

    def candles_summary(self, coin: str, interval: str) -> dict[str, Any]:
        """Cheap coverage summary for diagnostics — row count + min/max bar open."""
        coin_dir = self._candles_dir / coin / interval
        if not coin_dir.exists():
            return {"coin": coin, "interval": interval, "rows": 0}
        rows = 0
        min_ms: int | None = None
        max_ms: int | None = None
        for pf in coin_dir.rglob("data.parquet"):
            table = pq.read_table(pf, columns=["ts_open_ms"])
            if table.num_rows == 0:
                continue
            rows += table.num_rows
            col = table.column("ts_open_ms").to_pylist()
            local_min, local_max = min(col), max(col)
            min_ms = local_min if min_ms is None else min(min_ms, local_min)
            max_ms = local_max if max_ms is None else max(max_ms, local_max)
        return {
            "coin": coin,
            "interval": interval,
            "rows": rows,
            "first_ts_iso": _iso(min_ms) if min_ms is not None else None,
            "last_ts_iso": _iso(max_ms) if max_ms is not None else None,
        }

    # ------------------------------------------------------------------
    # Funding
    # ------------------------------------------------------------------

    def write_funding(self, coin: str, entries: list[dict[str, Any]]) -> int:
        if not entries:
            return 0
        records = [
            {
                "coin": coin,
                "ts_ms": int(e["time"]),
                "funding_rate": float(e["fundingRate"]),
                "premium": float(e.get("premium") or 0.0),
            }
            for e in entries
        ]
        df = pd.DataFrame.from_records(records)
        df["dt"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)

        rows_written = 0
        for year, part in df.groupby(df["dt"].dt.year):
            path = self._funding_partition_path(coin, int(year))
            merged = self._merge_partition(path, part.drop(columns=["dt"]), key="ts_ms")
            table = pa.Table.from_pandas(merged, schema=FUNDING_SCHEMA, preserve_index=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path, compression="snappy")
            rows_written += len(merged)
        return rows_written

    def read_funding(self, coin: str, start: datetime, end: datetime) -> pd.DataFrame:
        coin_dir = self._funding_dir / coin
        if not coin_dir.exists():
            return pd.DataFrame(columns=["coin", "ts_ms", "funding_rate", "premium", "dt"])
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        frames: list[pd.DataFrame] = []
        for pf in sorted(coin_dir.rglob("data.parquet")):
            df = pq.read_table(pf).to_pandas()
            df = df[(df["ts_ms"] >= start_ms) & (df["ts_ms"] <= end_ms)]
            if not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=["coin", "ts_ms", "funding_rate", "premium", "dt"])
        out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_ms"])
        out = out.sort_values("ts_ms")
        out["dt"] = pd.to_datetime(out["ts_ms"], unit="ms", utc=True)
        return out.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Universe snapshots
    # ------------------------------------------------------------------

    def write_universe_snapshot(
        self,
        snapshot_date: str,
        rows: list[dict[str, Any]],
    ) -> int:
        """`rows` are full UniverseEntry dicts (see hl_universe.compute_universe)."""
        if not rows:
            return 0
        df = pd.DataFrame.from_records(rows)
        df["snapshot_date"] = snapshot_date
        path = self._universe_dir / f"{snapshot_date}.parquet"
        # Cast and order columns to match the schema
        df = df.reindex(columns=[f.name for f in UNIVERSE_SNAPSHOT_SCHEMA])
        # Fill NaNs with sane defaults for ints
        df["rank"] = df["rank"].fillna(0).astype("int32")
        df["sz_decimals"] = df["sz_decimals"].fillna(0).astype("int32")
        df["max_leverage"] = df["max_leverage"].fillna(0).astype("int32")
        table = pa.Table.from_pandas(df, schema=UNIVERSE_SNAPSHOT_SCHEMA, preserve_index=False)
        pq.write_table(table, path, compression="snappy")
        return len(df)

    def list_universe_snapshots(self) -> list[str]:
        files = sorted(self._universe_dir.glob("*.parquet"))
        return [p.stem for p in files]

    def read_universe_snapshot(self, snapshot_date: str) -> pd.DataFrame:
        path = self._universe_dir / f"{snapshot_date}.parquet"
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in UNIVERSE_SNAPSHOT_SCHEMA])
        return pq.read_table(path).to_pandas()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _candle_partition_path(
        self, coin: str, interval: str, year: int, month: int
    ) -> Path:
        return (
            self._candles_dir
            / coin
            / interval
            / f"{year:04d}"
            / f"{month:02d}"
            / "data.parquet"
        )

    def _funding_partition_path(self, coin: str, year: int) -> Path:
        return self._funding_dir / coin / f"{year:04d}" / "data.parquet"

    @staticmethod
    def _merge_partition(path: Path, new_df: pd.DataFrame, key: str) -> pd.DataFrame:
        if path.exists():
            existing = pq.read_table(path).to_pandas()
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df
        combined = combined.drop_duplicates(subset=[key], keep="last")
        return combined.sort_values(key).reset_index(drop=True)

    def _read_resampled_candles(
        self,
        coin: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame | None:
        if interval not in {"2h", "3h", "4h"}:
            return None
        bucket_hours = int(interval.removesuffix("h"))
        source_start = _floor_to_hour_bucket(start, bucket_hours)
        source = self.read_candles(coin, "1h", source_start, end)
        if source.empty:
            return None
        return _resample_hourly_candles(
            source,
            coin=coin,
            interval=interval,
            bucket_hours=bucket_hours,
            start=start,
            end=end,
        )


def _candle_record(coin: str, interval: str, c: dict[str, Any]) -> dict[str, Any]:
    return {
        "coin": coin,
        "interval": interval,
        "ts_open_ms": int(c["t"]),
        "ts_close_ms": int(c["T"]),
        "open": float(c["o"]),
        "high": float(c["h"]),
        "low": float(c["l"]),
        "close": float(c["c"]),
        "volume": float(c["v"]),
        "n_trades": int(c.get("n", 0)),
    }


def _empty_candle_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[f.name for f in CANDLES_SCHEMA] + ["dt_open", "dt_close"]
    )


def _floor_to_hour_bucket(ts: datetime, bucket_hours: int) -> datetime:
    ts = ts.astimezone(UTC)
    return ts.replace(
        hour=(ts.hour // bucket_hours) * bucket_hours,
        minute=0,
        second=0,
        microsecond=0,
    )


def _resample_hourly_candles(
    source: pd.DataFrame,
    *,
    coin: str,
    interval: str,
    bucket_hours: int,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    if source.empty:
        return _empty_candle_df()
    work = source.copy()
    work["bucket_open"] = work["dt_open"].dt.floor(f"{bucket_hours}h")
    grouped = work.groupby("bucket_open", sort=True, dropna=False)

    rows: list[dict[str, Any]] = []
    for bucket_open, part in grouped:
        part = part.sort_values("ts_open_ms")
        if len(part) != bucket_hours:
            continue
        expected_close_ms = int(bucket_open.timestamp() * 1000) + (bucket_hours * 3_600_000) - 1
        if int(part.iloc[-1]["ts_close_ms"]) != expected_close_ms:
            continue
        rows.append(
            {
                "coin": coin,
                "interval": interval,
                "ts_open_ms": int(part.iloc[0]["ts_open_ms"]),
                "ts_close_ms": int(part.iloc[-1]["ts_close_ms"]),
                "open": float(part.iloc[0]["open"]),
                "high": float(part["high"].max()),
                "low": float(part["low"].min()),
                "close": float(part.iloc[-1]["close"]),
                "volume": float(part["volume"].sum()),
                "n_trades": int(part["n_trades"].sum()),
                "dt_open": bucket_open,
                "dt_close": bucket_open + timedelta(hours=bucket_hours) - timedelta(milliseconds=1),
            }
        )

    if not rows:
        return _empty_candle_df()

    out = pd.DataFrame.from_records(rows)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    out = out[(out["ts_open_ms"] >= start_ms) & (out["ts_open_ms"] <= end_ms)]
    if out.empty:
        return _empty_candle_df()
    return out.reset_index(drop=True)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()
