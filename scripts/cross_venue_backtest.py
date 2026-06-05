#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.runner.cross_venue_backtest import build_cross_venue_backtest_report



def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)



def main() -> int:
    parser = argparse.ArgumentParser(description="Build a synchronized cross-venue backtest report for HL/PM hypotheses.")
    parser.add_argument("--file", required=True, help="Path to cross-venue hypothesis markdown file")
    parser.add_argument("--start", required=True, help="UTC date or datetime ISO string")
    parser.add_argument("--end", required=True, help="UTC date or datetime ISO string")
    parser.add_argument("--data-dir", type=Path, default=Path("data/parquet"))
    parser.add_argument("--hl-interval", default="1h")
    args = parser.parse_args()

    report = build_cross_venue_backtest_report(
        Path(args.file),
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        data_dir=args.data_dir,
        hl_interval=args.hl_interval,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
