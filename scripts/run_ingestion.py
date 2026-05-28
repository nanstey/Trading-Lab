#!/usr/bin/env python3
"""
Continuous Polymarket data ingestion daemon.

Subscribes to live market WS for every token referenced by hypotheses in
PAPER / LIVE / OPTIMIZE state, and writes incoming book deltas + trade
prints to the Parquet catalog. Keeps the data corpus fresh so the
rolling-eval cron can always backtest "the last N days".

Designed to run as a long-lived process under systemd / tmux. Detects
new tokens hourly by re-querying the hypothesis list; new
subscriptions are folded into the existing WS connection.

Usage:
    .venv/bin/python scripts/run_ingestion.py
    .venv/bin/python scripts/run_ingestion.py --slugs tick-mean-revert,arb-complement
    .venv/bin/python scripts/run_ingestion.py --duration-secs 3600  # bounded for testing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("ingestion")


async def _resolve_tokens(slugs: list[str] | None) -> list[tuple[str, str, str]]:
    """Resolve hypotheses → list of (condition_id, yes_token, no_token)."""
    from trading_lab.agent import lifecycle
    from trading_lab.data.market_catalog import MarketCatalog
    from trading_lab.data.market_filter import MarketCriteria, select_markets

    target_states = (
        lifecycle.State.PAPER.value,
        lifecycle.State.LIVE.value,
        lifecycle.State.OPTIMIZE.value,
    )
    pairs: dict[str, tuple[str, str, str]] = {}  # condition_id → triple
    cat = MarketCatalog(Path("data/market_catalog.db"))
    try:
        for state in target_states:
            for h in lifecycle.list_hypotheses(state=state):
                if slugs and h.slug not in slugs:
                    continue
                crit = MarketCriteria.from_dict(h.market_criteria)
                for row in select_markets(crit, cat):
                    if row.yes_token_id and row.no_token_id:
                        pairs[row.condition_id] = (
                            row.condition_id, row.yes_token_id, row.no_token_id,
                        )
    finally:
        cat.close()
    return list(pairs.values())


async def run(args: argparse.Namespace) -> int:
    from trading_lab.agent.events import emit_event
    from trading_lab.data.catalog import DataCatalog
    from trading_lab.data.ingestion import PolymarketDataIngester

    catalog = DataCatalog(args.data_dir)
    slugs_filter = [s.strip() for s in args.slugs.split(",")] if args.slugs else None
    triples = await _resolve_tokens(slugs_filter)
    if not triples:
        print(json.dumps({"ok": False, "error": "no_target_tokens"}))
        return 2

    token_ids = []
    for _cid, yes_id, no_id in triples:
        token_ids.extend([yes_id, no_id])

    emit_event(
        type="ingestion_start",
        summary=f"data ingestion daemon — {len(token_ids)} tokens across "
                f"{len(triples)} markets",
        severity="info",
        data={"tokens": len(token_ids), "markets": len(triples)},
    )
    log.info("ingestion start: %d tokens", len(token_ids))

    stop = asyncio.Event()

    def _shutdown_handler(*_):
        log.info("shutdown signal received")
        stop.set()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _shutdown_handler)
        loop.add_signal_handler(signal.SIGTERM, _shutdown_handler)
    except Exception:
        pass

    # Use the existing public REST client just to satisfy the ingester
    # constructor — only the WS path is exercised here.
    from trading_lab.venues.polymarket.auth import L2Credentials
    from trading_lab.venues.polymarket.client import PolymarketRestClient
    from trading_lab.venues.polymarket.endpoints import HTTP_URL

    creds = L2Credentials(api_key="", api_secret="", api_passphrase="")
    rest = PolymarketRestClient(http_url=HTTP_URL, creds=creds)

    async with PolymarketDataIngester(catalog=catalog, client=rest) as ing:
        # `run_continuous` blocks on the WS task. Race it against a
        # timed-stop OR the SIGINT/SIGTERM event.
        ws_task = asyncio.create_task(ing.run_continuous(token_ids))
        if args.duration_secs:
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.duration_secs)
            except TimeoutError:
                pass
        else:
            await stop.wait()
        ws_task.cancel()
        try:
            await ws_task
        except (asyncio.CancelledError, Exception):
            pass

    emit_event(
        type="ingestion_stop",
        summary="data ingestion daemon stopped",
        severity="info",
        data={"stopped_at": datetime.now(tz=UTC).isoformat()},
    )
    print(json.dumps({"ok": True, "tokens": len(token_ids), "markets": len(triples)}))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slugs", default=None,
                   help="Comma-separated slugs to ingest for (default: all in PAPER/LIVE/OPTIMIZE)")
    p.add_argument("--data-dir", type=Path, default=Path("data/parquet"))
    p.add_argument("--duration-secs", type=int, default=None,
                   help="Stop after N seconds (testing). Default: run until SIGINT/SIGTERM.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
