#!/usr/bin/env python3
"""Build an aligned Polymarket 5m research dataset.

Creates one Parquet dataset per selected market by aligning:
- Chainlink reference prices
- Binance reference prices
- YES / NO best bid+ask snapshots
- YES / NO last trade prices

This is intended as a first research-grade dataset builder, not a perfect
execution simulator.

Usage:
    .venv/bin/python scripts/build_polymarket_5m_dataset.py --condition-id <CID>
    .venv/bin/python scripts/build_polymarket_5m_dataset.py --assets BTC --active-only --limit 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.data.catalog import DataCatalog
from trading_lab.data.reference_catalog import ReferencePriceCatalog
from trading_lab.research.polymarket_5m import (
    PM5mMarket,
    find_market_by_condition_id,
    select_polymarket_5m_markets,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=Path("data/market_catalog.db"))
    p.add_argument("--data-dir", type=Path, default=Path("data/parquet"))
    p.add_argument("--output-dir", type=Path, default=Path("research/datasets/polymarket_5m"))
    p.add_argument("--condition-id", action="append", default=[], help="Condition ID(s) to build explicitly.")
    p.add_argument("--assets", default="", help="Comma list of assets when auto-selecting, e.g. BTC,ETH.")
    p.add_argument("--active-only", action="store_true")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--sample-ms", type=int, default=1000, help="Sampling interval in ms (default: 1000).")
    p.add_argument("--pre-secs", type=int, default=60)
    p.add_argument("--post-secs", type=int, default=10)
    p.add_argument("--pretty", action="store_true")
    return p.parse_args()


def _resolve_markets(args: argparse.Namespace) -> list[PM5mMarket]:
    if args.condition_id:
        out: list[PM5mMarket] = []
        for cid in args.condition_id:
            market = find_market_by_condition_id(args.db, cid)
            if market is not None:
                out.append(market)
        return out
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()] or None
    return select_polymarket_5m_markets(
        args.db,
        assets=assets,
        active_only=args.active_only,
        include_closed=True,
        limit=args.limit,
    )


def _book_quotes(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["ts_ms", f"{prefix}_best_bid", f"{prefix}_best_ask"])
    bids = df[df["side"] == "bid"].groupby("timestamp", as_index=False)["price"].max()
    bids = bids.rename(columns={"timestamp": "ts_ms", "price": f"{prefix}_best_bid"})
    asks = df[df["side"] == "ask"].groupby("timestamp", as_index=False)["price"].min()
    asks = asks.rename(columns={"timestamp": "ts_ms", "price": f"{prefix}_best_ask"})
    out = pd.merge(bids, asks, on="ts_ms", how="outer")
    return out.sort_values("ts_ms").reset_index(drop=True)


def _last_trade(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["ts_ms", f"{prefix}_last_trade"])
    out = df.groupby("timestamp", as_index=False)["price"].last()
    return out.rename(columns={"timestamp": "ts_ms", "price": f"{prefix}_last_trade"}).sort_values("ts_ms")


def _asof_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    prefix: str,
    *,
    rename_values: bool = True,
) -> pd.DataFrame:
    if right.empty:
        return left.copy()
    cols = [c for c in right.columns if c != "ts_ms"]
    if rename_values:
        rename_map = {c: f"{prefix}_{c}" for c in cols}
    else:
        rename_map = {}
    right_on = f"{prefix}_src_ts_ms"
    renamed = right.rename(columns={"ts_ms": right_on, **rename_map})
    return pd.merge_asof(
        left.sort_values("ts_ms"),
        renamed.sort_values(right_on),
        left_on="ts_ms",
        right_on=right_on,
        direction="backward",
    )


def _value_asof(df: pd.DataFrame, ts_ms: int) -> float | None:
    if df.empty:
        return None
    eligible = df[df["ts_ms"] <= ts_ms]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["value"])


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.strip().lower()).strip("-")


def _build_one(
    market: PM5mMarket,
    *,
    data_catalog: DataCatalog,
    ref_catalog: ReferencePriceCatalog,
    sample_ms: int,
    pre_secs: int,
    post_secs: int,
    output_dir: Path,
) -> dict[str, Any]:
    start_dt = datetime.fromisoformat(market.event_start_iso.replace("Z", "+00:00")).astimezone(UTC)
    end_dt = datetime.fromisoformat(market.event_end_iso.replace("Z", "+00:00")).astimezone(UTC)
    query_start = start_dt - timedelta(seconds=pre_secs)
    query_end = end_dt + timedelta(seconds=post_secs)

    yes_book = data_catalog.read_orderbook_history(market.yes_token_id, query_start, query_end)
    no_book = data_catalog.read_orderbook_history(market.no_token_id, query_start, query_end)
    yes_trades = data_catalog.read_trades(market.yes_token_id, query_start, query_end)
    no_trades = data_catalog.read_trades(market.no_token_id, query_start, query_end)
    chainlink = ref_catalog.read_ticks("chainlink", market.chainlink_symbol, query_start, query_end)
    binance = ref_catalog.read_ticks("binance", market.binance_symbol, query_start, query_end)

    query_start_ms = int(query_start.timestamp() * 1000)
    query_end_ms = int(query_end.timestamp() * 1000)
    grid = pd.DataFrame({"ts_ms": list(range(query_start_ms, query_end_ms + 1, sample_ms))})
    yes_quotes = _book_quotes(yes_book, "yes")
    no_quotes = _book_quotes(no_book, "no")
    yes_last = _last_trade(yes_trades, "yes")
    no_last = _last_trade(no_trades, "no")
    chainlink_small = chainlink[["ts_ms", "value"]].copy() if not chainlink.empty else pd.DataFrame(columns=["ts_ms", "value"])
    binance_small = binance[["ts_ms", "value"]].copy() if not binance.empty else pd.DataFrame(columns=["ts_ms", "value"])

    df = grid.copy()
    df = _asof_join(df, chainlink_small, "chainlink")
    df = _asof_join(df, binance_small, "binance")
    df = _asof_join(df, yes_quotes, "yes_quotes", rename_values=False)
    df = _asof_join(df, no_quotes, "no_quotes", rename_values=False)
    df = _asof_join(df, yes_last, "yes_last", rename_values=False)
    df = _asof_join(df, no_last, "no_last", rename_values=False)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    chainlink_start = _value_asof(chainlink_small, start_ms)
    binance_start = _value_asof(binance_small, start_ms)

    df["condition_id"] = market.condition_id
    df["market_slug"] = market.market_slug
    df["asset"] = market.asset
    df["event_start_ms"] = start_ms
    df["event_end_ms"] = end_ms
    df["ms_to_start"] = df["ts_ms"] - start_ms
    df["ms_to_end"] = end_ms - df["ts_ms"]
    if chainlink_start is not None and "chainlink_value" in df.columns:
        df["chainlink_gap_from_start"] = df["chainlink_value"] - chainlink_start
    if binance_start is not None and "binance_value" in df.columns:
        df["binance_gap_from_start"] = df["binance_value"] - binance_start
    df["window_active"] = (df["ts_ms"] >= start_ms) & (df["ts_ms"] <= end_ms)

    asset_dir = output_dir / market.asset
    asset_dir.mkdir(parents=True, exist_ok=True)
    output_path = asset_dir / f"{_slugify(market.market_slug or market.condition_id)}.parquet"
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, output_path, compression="snappy")

    return {
        "condition_id": market.condition_id,
        "asset": market.asset,
        "market_slug": market.market_slug,
        "output_path": str(output_path),
        "rows": len(df),
        "yes_book_rows": len(yes_book),
        "no_book_rows": len(no_book),
        "yes_trade_rows": len(yes_trades),
        "no_trade_rows": len(no_trades),
        "chainlink_rows": len(chainlink),
        "binance_rows": len(binance),
        "event_start_iso": market.event_start_iso,
        "event_end_iso": market.event_end_iso,
    }


def main() -> int:
    args = _parse_args()
    markets = _resolve_markets(args)
    data_catalog = DataCatalog(args.data_dir)
    ref_catalog = ReferencePriceCatalog(args.data_dir)
    results = [
        _build_one(
            market,
            data_catalog=data_catalog,
            ref_catalog=ref_catalog,
            sample_ms=args.sample_ms,
            pre_secs=args.pre_secs,
            post_secs=args.post_secs,
            output_dir=args.output_dir,
        )
        for market in markets
    ]
    payload = {
        "ok": True,
        "type": "polymarket_5m_dataset_builder",
        "markets_requested": len(markets),
        "results": results,
        "output_dir": str(args.output_dir),
        "data_dir": str(args.data_dir),
        "sample_ms": args.sample_ms,
        "pre_secs": args.pre_secs,
        "post_secs": args.post_secs,
    }
    print(json.dumps(payload, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
