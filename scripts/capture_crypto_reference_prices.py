#!/usr/bin/env python3
"""Capture external crypto reference prices for Polymarket 5m research.

Subscribes to Polymarket RTDS crypto streams and archives Binance and/or
Chainlink price updates into the reference-price Parquet catalog.

Usage:
    .venv/bin/python scripts/capture_crypto_reference_prices.py --symbols BTC --duration-secs 30
    .venv/bin/python scripts/capture_crypto_reference_prices.py --symbols BTC,ETH --topics chainlink,binance
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.data.reference_catalog import ReferencePriceCatalog

RTDS_URL = "wss://ws-live-data.polymarket.com"
LOG = logging.getLogger("capture_crypto_reference_prices")

SYMBOL_MAP = {
    "BTC": {"binance": "btcusdt", "chainlink": "btc/usd"},
    "ETH": {"binance": "ethusdt", "chainlink": "eth/usd"},
    "SOL": {"binance": "solusdt", "chainlink": "sol/usd"},
    "XRP": {"binance": "xrpusdt", "chainlink": "xrp/usd"},
}
TOPIC_MAP = {
    "binance": {"topic": "crypto_prices", "type": "update"},
    "chainlink": {"topic": "crypto_prices_chainlink", "type": "*"},
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="BTC", help="Comma list of assets: BTC,ETH,SOL,XRP.")
    p.add_argument("--topics", default="binance,chainlink", help="Comma list: binance,chainlink.")
    p.add_argument("--data-dir", type=Path, default=Path("data/parquet"))
    p.add_argument("--duration-secs", type=int, default=60, help="Run time bound. Default: 60 seconds.")
    p.add_argument("--max-messages", type=int, default=0, help="Stop after N captured updates (0 = no cap).")
    p.add_argument("--flush-every", type=int, default=100, help="Flush tick buffer every N messages.")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


def _norm_list(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _norm_topics(raw: str) -> list[str]:
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _build_subscriptions(symbols: list[str], topics: list[str]) -> list[dict[str, Any]]:
    subscriptions: list[dict[str, Any]] = []
    for topic in topics:
        meta = TOPIC_MAP[topic]
        if topic == "binance":
            # RTDS rejects the documented plain-string filter form for Binance.
            # Subscribe to the topic and filter symbols client-side.
            subscriptions.append({"topic": meta["topic"], "type": meta["type"]})
            continue
        for symbol in symbols:
            subscriptions.append(
                {
                    "topic": meta["topic"],
                    "type": meta["type"],
                    "filters": json.dumps({"symbol": SYMBOL_MAP[symbol]["chainlink"]}),
                }
            )
    return subscriptions


async def _ping_loop(ws, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(5)
        try:
            await ws.send("PING")
        except Exception:
            return


def _flush_buffer(catalog: ReferencePriceCatalog, buffer: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for (source, symbol), ticks in list(buffer.items()):
        if not ticks:
            continue
        stored = catalog.write_ticks(source, symbol, ticks)
        summary[f"{source}:{symbol}"] = stored
        buffer[(source, symbol)] = []
    return summary


async def _run_capture(args: argparse.Namespace) -> dict[str, Any]:
    import websockets

    symbols = _norm_list(args.symbols)
    topics = _norm_topics(args.topics)
    for symbol in symbols:
        if symbol not in SYMBOL_MAP:
            raise ValueError(f"unsupported symbol: {symbol}")
    for topic in topics:
        if topic not in TOPIC_MAP:
            raise ValueError(f"unsupported topic: {topic}")

    catalog = ReferencePriceCatalog(args.data_dir)
    subs = _build_subscriptions(symbols, topics)
    allowed_symbols = {
        "binance": {SYMBOL_MAP[s]["binance"] for s in symbols},
        "chainlink": {SYMBOL_MAP[s]["chainlink"] for s in symbols},
    }
    stop = asyncio.Event()
    buffer: dict[tuple[str, str], list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    captured = 0

    def _shutdown(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _shutdown)
        loop.add_signal_handler(signal.SIGTERM, _shutdown)
    except Exception:
        pass

    async with websockets.connect(RTDS_URL, open_timeout=15, ping_interval=None) as ws:
        await ws.send(json.dumps({"action": "subscribe", "subscriptions": subs}))
        ping_task = asyncio.create_task(_ping_loop(ws, stop))
        try:
            deadline = None if args.duration_secs <= 0 else loop.time() + args.duration_secs
            while not stop.is_set():
                if deadline is not None and loop.time() >= deadline:
                    break
                timeout = 1.0 if deadline is None else max(0.1, min(1.0, deadline - loop.time()))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                topic = str(msg.get("topic") or "")
                msg_type = str(msg.get("type") or "")
                payload = msg.get("payload") or {}
                if msg_type == "subscribe" and isinstance(payload.get("data"), list) and payload.get("symbol"):
                    symbol = str(payload.get("symbol") or "").lower()
                    if symbol in allowed_symbols["chainlink"]:
                        key = ("chainlink", symbol)
                        batch = [
                            {"ts_ms": int(item["timestamp"]), "value": float(item["value"])}
                            for item in payload.get("data", [])
                            if item.get("timestamp") is not None and item.get("value") is not None
                        ]
                        if batch:
                            buffer.setdefault(key, []).extend(batch)
                            counts[f"chainlink:{symbol}"] = counts.get(f"chainlink:{symbol}", 0) + len(batch)
                            captured += len(batch)
                            if args.flush_every > 0 and captured % args.flush_every == 0:
                                _flush_buffer(catalog, buffer)
                            if args.max_messages > 0 and captured >= args.max_messages:
                                break
                    continue
                symbol = str(payload.get("symbol") or "").lower()
                ts_ms = payload.get("timestamp")
                value = payload.get("value")
                if topic == "crypto_prices":
                    source = "binance"
                elif topic == "crypto_prices_chainlink":
                    source = "chainlink"
                else:
                    continue
                if symbol not in allowed_symbols[source]:
                    continue
                if not symbol or ts_ms is None or value is None:
                    continue
                key = (source, symbol)
                buffer.setdefault(key, []).append({"ts_ms": int(ts_ms), "value": float(value)})
                counts[f"{source}:{symbol}"] = counts.get(f"{source}:{symbol}", 0) + 1
                captured += 1
                if args.flush_every > 0 and captured % args.flush_every == 0:
                    _flush_buffer(catalog, buffer)
                if args.max_messages > 0 and captured >= args.max_messages:
                    break
        finally:
            stop.set()
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    stored = _flush_buffer(catalog, buffer)
    return {
        "ok": True,
        "type": "capture_crypto_reference_prices",
        "rtds_url": RTDS_URL,
        "symbols": symbols,
        "topics": topics,
        "captured_messages": captured,
        "counts": counts,
        "stored": stored,
        "summaries": [
            catalog.ticks_summary(source, symbol)
            for source in topics
            for symbol in [SYMBOL_MAP[s][source] for s in symbols]
        ],
    }


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    result = asyncio.run(_run_capture(args))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
