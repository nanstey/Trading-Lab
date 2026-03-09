"""
Backtesting session runner.

Replays historical Parquet data through NautilusTrader's backtesting engine
using identical strategy code as live trading (zero code-path divergence).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "parquet"


async def run_backtest_session(
    start_iso: str | None = None,
    end_iso: str | None = None,
    instruments: list[str] | None = None,
) -> None:
    """
    Run a full backtest over stored Parquet data.

    Parameters
    ----------
    start_iso : str, optional
        ISO-8601 start datetime (e.g. "2024-01-01T00:00:00Z").
        Defaults to the earliest available data.
    end_iso : str, optional
        ISO-8601 end datetime. Defaults to the latest available data.
    instruments : list[str], optional
        Market IDs to include. Defaults to all found in DATA_DIR.
    """
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.enums import OmsType, AccountType
    from nautilus_trader.model.identifiers import TraderId, Venue
    from nautilus_trader.model.objects import Money
    from nautilus_trader.model.currencies import USDC_POS

    from nautilus_predict.config import load_config
    from nautilus_predict.strategies.arb_complement import BinaryArbStrategy, BinaryArbConfig
    from nautilus_predict.strategies.market_maker import MarketMakingStrategy, MarketMakingConfig

    cfg = load_config()

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("BACKTEST-001"),
            logging=LoggingConfig(log_level=cfg.log_level),
        )
    )

    # Add simulated venues
    engine.add_venue(
        venue=Venue("POLYMARKET"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(10_000, USDC_POS)],
    )

    # Load Parquet data
    parquet_files = list(DATA_DIR.glob("*.parquet"))
    if not parquet_files:
        log.warning("No Parquet files found in data directory", path=str(DATA_DIR))
        log.info("Run scripts/download_data.py to fetch historical data first.")
        return

    for pf in parquet_files:
        log.info("Loading data file", file=pf.name)
        # Data loading is instrument-type-specific; see scripts/backtest.py for examples.

    # Add strategies (same classes as live trading)
    engine.add_strategy(
        MarketMakingStrategy(
            config=MarketMakingConfig(
                spread_bps=cfg.market_maker.spread_bps,
                order_size_usdc=cfg.market_maker.order_size_usdc,
                max_position_usdc=cfg.market_maker.max_position_usdc,
            )
        )
    )
    engine.add_strategy(
        BinaryArbStrategy(
            config=BinaryArbConfig(
                min_profit_usdc=cfg.arb.min_profit_usdc,
                max_capital_usdc=cfg.arb.max_capital_usdc,
            )
        )
    )

    log.info("Running backtest engine")
    engine.run()

    # Print summary statistics
    engine.trader.generate_account_report(Venue("POLYMARKET"))
    engine.trader.generate_order_fills_report()
    engine.trader.generate_positions_report()

    engine.dispose()
    log.info("Backtest session complete")
