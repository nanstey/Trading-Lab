#!/usr/bin/env python3
"""List Hyperliquid outcome/prediction markets as JSON.

Read-only operator script for inspecting Hyperliquid's live outcome-market
surface before wiring strategies or runners.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.config import load_config
from trading_lab.venues.hyperliquid.client import HyperliquidRestClient
from trading_lab.venues.hyperliquid.outcome_client import HyperliquidOutcomeClient
from trading_lab.venues.hyperliquid.outcomes import outcome_coin

_DUMMY_KEY = "0x" + "11" * 32


async def _run(network: str, limit: int, include_fallback: bool, with_books: bool) -> dict:
    cfg = load_config()
    endpoint = cfg.venues.hyperliquid.active(network)
    rest = HyperliquidRestClient(http_url=endpoint.api_url, private_key=_DUMMY_KEY)
    client = HyperliquidOutcomeClient(rest)
    try:
        universe = await client.get_universe(refresh=True)
        outcomes = await client.list_outcomes(include_fallback=include_fallback)
        outcomes = outcomes[:limit] if limit > 0 else outcomes
        rows = []
        for spec in outcomes:
            question = universe.question_for_outcome(spec.outcome)
            row = {
                "outcome": spec.outcome,
                "name": spec.name,
                "description": spec.description,
                "quote_token": spec.quote_token,
                "question_id": question.question if question else None,
                "question_name": question.name if question else None,
                "sides": [
                    {"side": side.side, "name": side.name, "coin": outcome_coin(spec.outcome, side.side)}
                    for side in spec.side_specs
                ],
            }
            if with_books:
                books = {}
                for side in (0, 1):
                    try:
                        book = await client.get_orderbook(spec.outcome, side)
                        best_bid = book.get("levels", [[], []])[0][:1]
                        best_ask = book.get("levels", [[], []])[1][:1]
                        books[str(side)] = {
                            "coin": book.get("coin"),
                            "time": book.get("time"),
                            "best_bid": best_bid[0] if best_bid else None,
                            "best_ask": best_ask[0] if best_ask else None,
                            "spread": book.get("spread"),
                        }
                    except Exception as exc:
                        books[str(side)] = {"error": str(exc)}
                row["books"] = books
            rows.append(row)
        return {
            "ok": True,
            "question_count": len(universe.questions),
            "outcome_count": len(universe.outcomes),
            "returned": len(rows),
            "outcomes": rows,
        }
    finally:
        await rest.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--network", choices=["mainnet", "testnet"], default="mainnet")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--include-fallback", action="store_true")
    p.add_argument("--with-books", action="store_true")
    args = p.parse_args()
    out = asyncio.run(_run(args.network, args.limit, args.include_fallback, args.with_books))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
