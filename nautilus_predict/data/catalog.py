"""
Parquet Data Catalog for Nautilus-Predict.

Stores historical order book snapshots and trades in Parquet format
organized by token_id. Used for backtesting and strategy research.

Directory layout:
    data/
        parquet/
            orderbooks/
                {token_id}/
                    {date}.parquet
            trades/
                {token_id}/
                    {date}.parquet

Uses PyArrow for efficient columnar storage and Pandas for analysis.
Data is partitioned by date for efficient time-range queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

# PyArrow schemas
ORDERBOOK_SCHEMA = pa.schema(
    [
        pa.field("timestamp", pa.int64()),
        pa.field("token_id", pa.string()),
        pa.field("side", pa.string()),   # "bid" or "ask"
        pa.field("price", pa.float64()),
        pa.field("size", pa.float64()),
    ]
)

TRADES_SCHEMA = pa.schema(
    [
        pa.field("timestamp", pa.int64()),
        pa.field("token_id", pa.string()),
        pa.field("trade_id", pa.string()),
        pa.field("side", pa.string()),   # "BUY" or "SELL"
        pa.field("price", pa.float64()),
        pa.field("size", pa.float64()),
        pa.field("fee", pa.float64()),
    ]
)


class DataCatalog:
    """
    PyArrow/Parquet data catalog for historical market data.

    Stores order book snapshots and trades organized by token_id and date.
    Supports efficient time-range queries for backtesting.

    Parameters
    ----------
    data_dir : Path
        Root directory for storing Parquet files. Typically ./data/parquet/.

    Example
    -------
    >>> catalog = DataCatalog(data_dir=Path("./data/parquet"))
    >>> catalog.write_orderbook_snapshot(
    ...     token_id="0xabc",
    ...     timestamp=1700000000000,
    ...     bids=[(0.60, 100.0)],
    ...     asks=[(0.61, 80.0)],
    ... )
    >>> df = catalog.read_orderbook_history("0xabc", start=dt_start, end=dt_end)
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._orderbooks_dir = data_dir / "orderbooks"
        self._trades_dir = data_dir / "trades"

        # Create directory structure
        self._orderbooks_dir.mkdir(parents=True, exist_ok=True)
        self._trades_dir.mkdir(parents=True, exist_ok=True)

    def write_orderbook_snapshot(
        self,
        token_id: str,
        timestamp: int,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
    ) -> None:
        """
        Append an order book snapshot to the Parquet catalog.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.
        timestamp : int
            Unix timestamp in milliseconds.
        bids : list[tuple[float, float]]
            List of (price, size) tuples for the bid side.
        asks : list[tuple[float, float]]
            List of (price, size) tuples for the ask side.
        """
        records: list[dict[str, Any]] = []

        for price, size in bids:
            records.append(
                {
                    "timestamp": timestamp,
                    "token_id": token_id,
                    "side": "bid",
                    "price": float(price),
                    "size": float(size),
                }
            )
        for price, size in asks:
            records.append(
                {
                    "timestamp": timestamp,
                    "token_id": token_id,
                    "side": "ask",
                    "price": float(price),
                    "size": float(size),
                }
            )

        if not records:
            return

        table = pa.Table.from_pylist(records, schema=ORDERBOOK_SCHEMA)
        date_str = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        token_dir = self._orderbooks_dir / self._safe_token_dir(token_id)
        token_dir.mkdir(parents=True, exist_ok=True)
        output_path = token_dir / f"{date_str}.parquet"

        self._append_or_write(output_path, table, ORDERBOOK_SCHEMA)
        log.debug(
            "Orderbook snapshot written",
            extra={"token_id": token_id, "records": len(records), "date": date_str},
        )

    def read_orderbook_history(
        self,
        token_id: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Read historical order book snapshots for a token.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.
        start : datetime
            Start of the time range (UTC).
        end : datetime
            End of the time range (UTC).

        Returns
        -------
        pd.DataFrame
            DataFrame with columns: timestamp, token_id, side, price, size.
            Sorted by timestamp ascending.
        """
        token_dir = self._orderbooks_dir / self._safe_token_dir(token_id)
        if not token_dir.exists():
            log.warning("No data found for token", extra={"token_id": token_id})
            return pd.DataFrame(columns=["timestamp", "token_id", "side", "price", "size"])

        parquet_files = sorted(token_dir.glob("*.parquet"))
        if not parquet_files:
            return pd.DataFrame(columns=["timestamp", "token_id", "side", "price", "size"])

        tables = []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        for pf in parquet_files:
            table = pq.read_table(pf)
            # Filter by timestamp range
            mask = (
                (table.column("timestamp") >= pa.scalar(start_ms))
                & (table.column("timestamp") <= pa.scalar(end_ms))
            )
            filtered = table.filter(mask)
            if filtered.num_rows > 0:
                tables.append(filtered)

        if not tables:
            return pd.DataFrame(columns=["timestamp", "token_id", "side", "price", "size"])

        combined = pa.concat_tables(tables)
        df = combined.to_pandas()
        return df.sort_values("timestamp").reset_index(drop=True)

    def write_trades(self, token_id: str, trades: list[dict[str, Any]]) -> None:
        """
        Write trade records to the Parquet catalog.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.
        trades : list[dict]
            Trade records. Each dict must have: timestamp, trade_id,
            side, price, size, fee.
        """
        if not trades:
            return

        # Normalize records
        records = [
            {
                "timestamp": int(t.get("timestamp", 0)),
                "token_id": token_id,
                "trade_id": str(t.get("trade_id", "")),
                "side": str(t.get("side", "")),
                "price": float(t.get("price", 0)),
                "size": float(t.get("size", 0)),
                "fee": float(t.get("fee", 0)),
            }
            for t in trades
        ]

        table = pa.Table.from_pylist(records, schema=TRADES_SCHEMA)

        if not records:
            return

        # Use date of first trade for partitioning
        first_ts = records[0]["timestamp"]
        date_str = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        token_dir = self._trades_dir / self._safe_token_dir(token_id)
        token_dir.mkdir(parents=True, exist_ok=True)
        output_path = token_dir / f"{date_str}.parquet"

        self._append_or_write(output_path, table, TRADES_SCHEMA)
        log.info(
            "Trades written",
            extra={"token_id": token_id, "count": len(records), "date": date_str},
        )

    def list_available_markets(self) -> list[str]:
        """
        Return a list of token IDs that have data in the catalog.

        Returns
        -------
        list[str]
            Token IDs with at least one Parquet file.
        """
        markets = set()

        for subdir in [self._orderbooks_dir, self._trades_dir]:
            if subdir.exists():
                for token_dir in subdir.iterdir():
                    if token_dir.is_dir() and any(token_dir.glob("*.parquet")):
                        markets.add(token_dir.name)

        return sorted(markets)

    def get_data_summary(self) -> dict[str, Any]:
        """
        Return a summary of all data in the catalog.

        Returns
        -------
        dict
            Summary with total markets, date ranges, and file counts.
        """
        markets = self.list_available_markets()
        total_files = sum(
            1
            for d in [self._orderbooks_dir, self._trades_dir]
            if d.exists()
            for f in d.rglob("*.parquet")
        )
        return {
            "total_markets": len(markets),
            "total_parquet_files": total_files,
            "markets": markets,
            "data_dir": str(self._data_dir),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_token_dir(token_id: str) -> str:
        """Convert a token ID to a filesystem-safe directory name."""
        # Strip 0x prefix and take first 16 chars to keep paths short
        safe = token_id.lstrip("0x").lstrip("0X")
        return safe[:32] if len(safe) > 32 else safe

    @staticmethod
    def _append_or_write(
        path: Path,
        table: pa.Table,
        schema: pa.Schema,
    ) -> None:
        """Append to existing Parquet file or create a new one."""
        if path.exists():
            existing = pq.read_table(path)
            combined = pa.concat_tables([existing, table])
            pq.write_table(combined, path, compression="snappy")
        else:
            pq.write_table(table, path, compression="snappy")
