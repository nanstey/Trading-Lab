"""
Entry point for Nautilus-Predict.

Supports three run modes:
  live      - Connect to real venues, execute live orders
  paper     - Connect to real venues, simulate order fills locally
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


async def _run_live_or_paper(config_mode: TradingMode) -> None:
    """Shared bootstrap for live and paper trading modes."""
    from nautilus_trader.live.node import TradingNode

    from nautilus_predict.node import build_node

    log.info("Starting trading node", mode=config_mode.value)
    node: TradingNode = build_node(config_mode)
    try:
        node.build()
        node.start()
        await asyncio.sleep(float("inf"))  # run until cancelled
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutdown signal received")
    finally:
        node.stop()
        node.dispose()
        log.info("Node disposed")


async def _run_backtest() -> None:
    """Run a backtesting session over stored Parquet data."""
    from nautilus_predict.backtest import run_backtest_session

    log.info("Starting backtest session")
    await run_backtest_session()
    log.info("Backtest complete")


def run_live() -> None:
    """Console script entry point for live trading."""
    cfg = load_config()
    _configure_logging(cfg.log_level)
    asyncio.run(_run_live_or_paper(TradingMode.LIVE))


def run_backtest() -> None:
    """Console script entry point for backtesting."""
    cfg = load_config()
    _configure_logging(cfg.log_level)
    asyncio.run(_run_backtest())


def main() -> None:
    parser = argparse.ArgumentParser(description="Nautilus-Predict trading system")
    parser.add_argument(
        "--mode",
        choices=["live", "paper", "backtest"],
        default="paper",
        help="Execution mode (default: paper)",
    )
    args = parser.parse_args()

    cfg = load_config()
    _configure_logging(cfg.log_level)

    mode = TradingMode(args.mode)
    log.info("Nautilus-Predict initialising", mode=mode.value, log_level=cfg.log_level)

    if mode == TradingMode.BACKTEST:
        asyncio.run(_run_backtest())
    else:
        asyncio.run(_run_live_or_paper(mode))


if __name__ == "__main__":
    sys.exit(main())
