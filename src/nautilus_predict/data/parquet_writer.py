"""
End-of-day Parquet writer for NautilusTrader data objects.

Persists OrderBookDeltas and TradeTicks to Parquet files in the NautilusTrader
catalog format so they can be replayed exactly in backtesting sessions.

Usage
-----
    writer = ParquetDataWriter(catalog_path=Path("catalog"))
    writer.write_order_book_deltas(deltas_list, instrument_id)
    writer.write_trade_ticks(ticks_list, instrument_id)
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

log = structlog.get_logger(__name__)

DEFAULT_CATALOG_PATH = Path("catalog")


class ParquetDataWriter:
    """
    Writes NautilusTrader data objects to Parquet files.

    Files are partitioned by instrument and date:
        catalog/
          data/
            order_book_delta/
              instrument_id=BTC-PERP.HYPERLIQUID/
                2024-01-15.parquet
    """

    def __init__(self, catalog_path: Path = DEFAULT_CATALOG_PATH) -> None:
        self._catalog = catalog_path
        self._catalog.mkdir(parents=True, exist_ok=True)

    def write_order_book_deltas(
        self,
        deltas: Sequence,
        instrument_id: str,
        date_str: str | None = None,
    ) -> Path:
        """Persist a batch of OrderBookDelta objects."""
        if not deltas:
            return Path()

        rows = []
        for d in deltas:
            rows.append(
                {
                    "instrument_id": str(d.instrument_id),
                    "action": d.action.value,
                    "side": d.order.side.value if d.order else 0,
                    "price": float(d.order.price) if d.order else 0.0,
                    "size": float(d.order.size) if d.order else 0.0,
                    "order_id": d.order.order_id if d.order else 0,
                    "flags": d.flags,
                    "sequence": d.sequence,
                    "ts_event": d.ts_event,
                    "ts_init": d.ts_init,
                }
            )

        table = pa.Table.from_pylist(rows)
        return self._write_table(table, "order_book_delta", instrument_id, date_str)

    def write_trade_ticks(
        self,
        ticks: Sequence,
        instrument_id: str,
        date_str: str | None = None,
    ) -> Path:
        """Persist a batch of TradeTick objects."""
        if not ticks:
            return Path()

        rows = [
            {
                "instrument_id": str(t.instrument_id),
                "price": float(t.price),
                "size": float(t.size),
                "aggressor_side": t.aggressor_side.value,
                "trade_id": str(t.trade_id),
                "ts_event": t.ts_event,
                "ts_init": t.ts_init,
            }
            for t in ticks
        ]

        table = pa.Table.from_pylist(rows)
        return self._write_table(table, "trade_tick", instrument_id, date_str)

    def _write_table(
        self,
        table: pa.Table,
        data_type: str,
        instrument_id: str,
        date_str: str | None,
    ) -> Path:
        from datetime import date

        if date_str is None:
            date_str = date.today().isoformat()

        safe_iid = instrument_id.replace("/", "-").replace(".", "_")
        out_dir = self._catalog / "data" / data_type / f"instrument_id={safe_iid}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{date_str}.parquet"

        pq.write_table(
            table,
            out_path,
            compression="snappy",
            write_statistics=True,
        )
        log.debug("Wrote Parquet file", path=str(out_path), rows=len(table))
        return out_path
