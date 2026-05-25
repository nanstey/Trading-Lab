"""
Entry point for Nautilus-Predict.

Supports three run modes:
  live      - Connect to real venues, execute live orders (gated)
  paper     - Stream live market data, simulate fills locally
  backtest  - Replay historical Parquet data, no network required
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from nautilus_predict.config import TradingMode, load_config

log = structlog.get_logger(__name__)


def _configure_logging(level: str) -> None:
    import logging

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def _select_default_pairs(slug: str = "arb-complement") -> list[tuple[str, str, str]]:
    """Pick paper-mode pairs from the hypothesis slug + market catalog."""
    from pathlib import Path

    from nautilus_predict.data.market_catalog import MarketCatalog
    from nautilus_predict.data.market_filter import MarketCriteria, select_markets
    from nautilus_predict.runner.backtest import _parse_hypothesis

    criteria_dict, _ = _parse_hypothesis(Path(f"research/hypotheses/{slug}.md"))
    if criteria_dict is None:
        return []
    criteria = MarketCriteria.from_dict(criteria_dict)
    cat = MarketCatalog(Path("data/market_catalog.db"))
    rows = select_markets(criteria, cat)
    cat.close()
    return [
        (r.condition_id, r.yes_token_id, r.no_token_id)
        for r in rows
        if r.yes_token_id and r.no_token_id
    ]


async def _run_paper(duration_secs: int | None) -> None:
    from nautilus_predict.runner.paper import PaperRunner

    cfg = load_config()
    pairs = _select_default_pairs()
    if not pairs:
        log.error("no paper pairs available — run `make sync-markets` first")
        return
    log.info("paper pairs selected", count=len(pairs))
    runner = PaperRunner(config=cfg, pairs=pairs, duration_secs=duration_secs)
    summary = await runner.run()
    log.info("paper summary", **summary.to_dict())


async def _run_live() -> None:
    """Live mode — gated by config + LIVE_TRADING_CONFIRMED."""
    from nautilus_trader.live.node import TradingNode

    from nautilus_predict.node import build_node

    log.info("Starting trading node", mode="live")
    node: TradingNode = build_node(TradingMode.LIVE)
    try:
        node.build()
        node.start()
        await asyncio.sleep(float("inf"))
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutdown signal received")
    finally:
        node.stop()
        node.dispose()
        log.info("Node disposed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Nautilus-Predict trading system")
    parser.add_argument(
        "--mode",
        choices=["live", "paper", "backtest"],
        default="paper",
    )
    parser.add_argument(
        "--duration-secs",
        type=int,
        default=None,
        help="Stop paper run after N seconds (default: run forever)",
    )
    args = parser.parse_args()

    cfg = load_config()
    _configure_logging(cfg.log_level)

    mode = TradingMode(args.mode)
    log.info("Nautilus-Predict initialising", mode=mode.value, log_level=cfg.log_level)

    if mode == TradingMode.BACKTEST:
        log.info("Backtest mode: use `python scripts/backtest.py` directly")
        return
    if mode == TradingMode.PAPER:
        asyncio.run(_run_paper(args.duration_secs))
        return
    asyncio.run(_run_live())


if __name__ == "__main__":
    sys.exit(main())
