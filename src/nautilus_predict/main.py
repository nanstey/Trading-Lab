"""
Entry point for the legacy `make paper` / `make live` / `make backtest`
targets. Prints a short notice + a pointer to the modern CLI scripts.

Paper / live are now per-strategy concerns (see hypothesis lifecycle
state), not a system-wide TRADING_MODE env var. Use:

  - `scripts/paper_run_v2.py --slug <slug>` for paper trading
  - `scripts/live_run.py --slug <slug>` for live trading
  - `scripts/backtest.py --hypothesis-slug <slug>` for backtests
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["live", "paper", "backtest"], default="paper",
    )
    args = parser.parse_args()

    if args.mode == "backtest":
        print(
            "Backtest mode: use `make research-test SLUG=<slug> "
            "START=YYYY-MM-DD END=YYYY-MM-DD` or `scripts/backtest.py "
            "--hypothesis-slug <slug>` directly.",
            file=sys.stderr,
        )
        return 0
    if args.mode == "paper":
        print(
            "Paper mode: use `make paper-run SLUG=<slug> [DURATION_SECS=600]`. "
            "The strategy must be in PAPER state (see "
            "`scripts/research_cli.py show --slug <slug>`).",
            file=sys.stderr,
        )
        return 0
    print(
        "Live mode: use `make live-run SLUG=<slug> CONFIRM=1` after setting "
        "LIVE_TRADING_CONFIRMED=true. The strategy must be in LIVE state.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
