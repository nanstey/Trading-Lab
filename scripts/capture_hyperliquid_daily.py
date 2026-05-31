#!/usr/bin/env python3
"""Incremental daily Hyperliquid data capture.

Purpose:
- refresh the current top-N universe snapshot
- capture trailing candles for 5m / 1h / 1d with overlap windows
- capture trailing funding history with overlap
- write one structured event for operator visibility

This job is idempotent by design: every run re-pulls an overlap window and
rewrites the touched parquet partitions via `HyperliquidCatalog`.

Usage:
    .venv/bin/python scripts/capture_hyperliquid_daily.py
    .venv/bin/python scripts/capture_hyperliquid_daily.py --top-n 25 --intervals 5m,1h
    .venv/bin/python scripts/capture_hyperliquid_daily.py --coins BTC,ETH,SOL
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent.events import emit_event
from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.data.hl_daily_capture import (
    build_daily_capture_plan,
    choose_capture_coins,
    latest_snapshot_coins,
    normalize_intervals,
)
from trading_lab.data.hl_universe import compute_universe, persist_snapshot
from trading_lab.venues.hyperliquid.endpoints import MAINNET_HTTP_URL
from trading_lab.venues.hyperliquid.historical import HyperliquidHistoricalClient

DEFAULT_DATA_DIR = Path("data/parquet")

log = logging.getLogger("hl_daily_capture")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--as-of", default=date.today().isoformat(), help="UTC date label YYYY-MM-DD.")
    p.add_argument("--top-n", type=int, default=20, help="Universe snapshot breadth.")
    p.add_argument("--coins", default="", help="Optional explicit comma list override.")
    p.add_argument("--intervals", default=",".join(("5m", "1h", "1d")))
    p.add_argument("--funding-lookback-days", type=int, default=30)
    p.add_argument("--lookback-5m-days", type=int, default=7)
    p.add_argument("--lookback-1h-days", type=int, default=14)
    p.add_argument("--lookback-1d-days", type=int, default=120)
    p.add_argument("--method", choices=["live_24h", "historical_30d"], default="live_24h")
    p.add_argument("--universe-lookback-days", type=int, default=30)
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def _parse_coin_list(raw: str) -> list[str]:
    return [c.strip().upper() for c in raw.split(",") if c.strip()]


async def _download_interval(
    *,
    client: HyperliquidHistoricalClient,
    catalog: HyperliquidCatalog,
    coin: str,
    interval: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    candles = await client.fetch_candles(coin, interval, start_ms, end_ms)
    fetched = len(candles)
    catalog.write_candles(coin, interval, candles)
    summary = catalog.candles_summary(coin, interval)
    return {
        "fetched": fetched,
        "stored_total": summary["rows"],
        "first": summary.get("first_ts_iso"),
        "last": summary.get("last_ts_iso"),
        "start": start_date,
        "end": end_date,
    }


async def _download_funding(
    *,
    client: HyperliquidHistoricalClient,
    catalog: HyperliquidCatalog,
    coin: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    funding = await client.fetch_funding_history(coin, start_ms, end_ms)
    written = catalog.write_funding(coin, funding)
    return {
        "fetched": len(funding),
        "stored_total": written,
        "start": start_date,
        "end": end_date,
    }


async def _capture_one_coin(
    *,
    client: HyperliquidHistoricalClient,
    catalog: HyperliquidCatalog,
    coin: str,
    plan,
) -> dict[str, Any]:
    result: dict[str, Any] = {"coin": coin, "intervals": {}, "errors": []}
    for window in plan.windows:
        try:
            result["intervals"][window.interval] = await _download_interval(
                client=client,
                catalog=catalog,
                coin=coin,
                interval=window.interval,
                start_date=window.start_date,
                end_date=window.end_date,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "hl_daily_capture_interval_failed coin=%s interval=%s", coin, window.interval
            )
            result["errors"].append(f"candles[{window.interval}]: {exc!r}")
    try:
        result["funding"] = await _download_funding(
            client=client,
            catalog=catalog,
            coin=coin,
            start_date=plan.funding_start_date,
            end_date=plan.funding_end_date,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("hl_daily_capture_funding_failed coin=%s", coin)
        result["errors"].append(f"funding: {exc!r}")
    return result


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = Path(args.data_dir)
    catalog = HyperliquidCatalog(data_dir)
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
    intervals = normalize_intervals([s.strip() for s in args.intervals.split(",") if s.strip()])
    plan = build_daily_capture_plan(
        as_of=as_of,
        intervals=list(intervals),
        interval_lookbacks={
            "5m": args.lookback_5m_days,
            "1h": args.lookback_1h_days,
            "1d": args.lookback_1d_days,
        },
        funding_lookback_days=args.funding_lookback_days,
    )

    async with HyperliquidHistoricalClient(MAINNET_HTTP_URL) as client:
        today_universe = await compute_universe(
            client,
            top_n=args.top_n,
            method=args.method,
            lookback_days=args.universe_lookback_days,
            catalog=catalog,
            as_of=datetime.combine(as_of, datetime.min.time(), tzinfo=UTC),
        )
        snapshot_path = persist_snapshot(catalog, args.as_of, today_universe)
        snapshot_coins = [u.coin for u in today_universe]
        previous_coins = latest_snapshot_coins(
            catalog.list_universe_snapshots()[:-1],
            catalog.read_universe_snapshot,
        )
        coins = choose_capture_coins(
            explicit=_parse_coin_list(args.coins),
            latest_snapshot_coins=previous_coins,
            today_snapshot_coins=snapshot_coins,
        )

        sem = asyncio.Semaphore(args.concurrency)

        async def _one(coin: str) -> dict[str, Any]:
            async with sem:
                return await _capture_one_coin(client=client, catalog=catalog, coin=coin, plan=plan)

        per_coin = await asyncio.gather(*[_one(coin) for coin in coins])

    summary = {
        "ok": True,
        "type": "hl_daily_capture",
        "as_of": args.as_of,
        "data_dir": str(data_dir),
        "top_n": args.top_n,
        "intervals": list(plan.intervals),
        "windows": [w.__dict__ for w in plan.windows],
        "funding_window": {
            "start": plan.funding_start_date,
            "end": plan.funding_end_date,
            "lookback_days": plan.funding_lookback_days,
        },
        "universe": {
            "path": str(snapshot_path),
            "method": args.method,
            "coins": snapshot_coins,
            "count": len(snapshot_coins),
        },
        "coins_captured": coins,
        "captured_count": len(coins),
        "per_coin": per_coin,
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        summary = asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}))
        return 130

    any_errors = any(item.get("errors") for item in summary["per_coin"])
    summary["ok"] = not any_errors
    print(json.dumps(summary, indent=2, default=str))

    emit_event(
        "hl_daily_capture",
        (
            f"HL daily capture {'completed' if not any_errors else 'completed_with_errors'}: "
            f"{summary['captured_count']} coins, intervals={','.join(summary['intervals'])}"
        ),
        severity="warn" if any_errors else "info",
        data={
            "as_of": summary["as_of"],
            "captured_count": summary["captured_count"],
            "intervals": summary["intervals"],
            "universe_count": summary["universe"]["count"],
            "errors": [
                {"coin": item["coin"], "errors": item.get("errors", [])}
                for item in summary["per_coin"]
                if item.get("errors")
            ],
        },
    )
    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
