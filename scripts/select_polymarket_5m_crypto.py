#!/usr/bin/env python3
"""Select recurring Polymarket 5-minute crypto Up/Down markets from the market catalog.

Purpose:
- identify candidate 5m crypto markets despite sparse catalog metadata
- emit token IDs, timing fields, and RTDS symbol hints for downstream capture
- optionally save the selector result to JSON for dataset-building workflows

Usage:
    .venv/bin/python scripts/select_polymarket_5m_crypto.py --active-only
    .venv/bin/python scripts/select_polymarket_5m_crypto.py --assets BTC,ETH --limit 10
    .venv/bin/python scripts/select_polymarket_5m_crypto.py --json-out /tmp/pm5m.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.research.polymarket_5m import select_polymarket_5m_markets


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=Path("data/market_catalog.db"))
    p.add_argument("--assets", default="BTC,ETH,SOL,XRP", help="Comma list of asset codes to keep.")
    p.add_argument("--active-only", action="store_true", help="Keep only currently active markets.")
    p.add_argument("--exclude-closed", action="store_true", help="Drop closed markets from the result.")
    p.add_argument("--limit", type=int, default=None, help="Optional result cap after filtering.")
    p.add_argument("--json-out", type=Path, default=None, help="Optional path to save the JSON result.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print stdout JSON.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    markets = select_polymarket_5m_markets(
        args.db,
        assets=assets,
        active_only=args.active_only,
        include_closed=not args.exclude_closed,
        limit=args.limit,
    )
    payload = {
        "ok": True,
        "type": "polymarket_5m_selector",
        "db": str(args.db),
        "assets": assets,
        "active_only": args.active_only,
        "exclude_closed": args.exclude_closed,
        "count": len(markets),
        "markets": [m.to_dict() for m in markets],
    }
    text = json.dumps(payload, indent=2 if args.pretty else None)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
