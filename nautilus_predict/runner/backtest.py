"""
Backtest Runner.

Runs trading strategies against historical Parquet data using
NautilusTrader's BacktestEngine. The key design principle is that
strategies run against IDENTICAL code paths in backtest and live modes -
there is no separate "backtest strategy" vs "live strategy". The engine
handles the mode abstraction.

Data flow:
    Parquet files → NautilusTrader DataEngine → Strategy.on_book_update()
    → Order signals → BacktestExecEngine (simulated fills) → Analytics

This ensures that any strategy validated in backtest will behave
identically in paper and live trading.

Usage:
    runner = BacktestRunner(config=TradingConfig())
    runner.run(
        strategy_class=ComplementArbStrategy,
        start_date="2024-01-01",
        end_date="2024-03-31",
        token_ids=["0xabc...", "0xdef..."],
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Type

from nautilus_predict.config import TradingConfig, TradingMode

log = logging.getLogger(__name__)


class BacktestRunner:
    """
    NautilusTrader-backed backtesting runner.

    Loads historical Parquet data and runs strategies through the
    NautilusTrader BacktestEngine. Produces fill reports and
    performance analytics.

    Parameters
    ----------
    config : TradingConfig
        System configuration. trading_mode is overridden to BACKTEST.

    Example
    -------
    >>> runner = BacktestRunner(config=TradingConfig())
    >>> runner.run(
    ...     strategy_class=ComplementArbStrategy,
    ...     start_date="2024-01-01",
    ...     end_date="2024-03-31",
    ...     token_ids=["0xabc..."],
    ... )
    """

    def __init__(self, config: TradingConfig) -> None:
        self._config = config
        self._data_dir = Path("data/parquet")

    def run(
        self,
        strategy_class: Type[Any],
        start_date: str,
        end_date: str,
        token_ids: list[str],
        initial_capital_usdc: float = 10_000.0,
    ) -> None:
        """
        Run a backtest for the given strategy and date range.

        Uses NautilusTrader's BacktestEngine to replay historical
        order book data through the strategy. Identical code path
        to live trading ensures no look-ahead bias from strategy code.

        Parameters
        ----------
        strategy_class : type
            Strategy class to instantiate and run (must subclass
            NautilusPredictStrategy).
        start_date : str
            ISO date string (YYYY-MM-DD) for backtest start.
        end_date : str
            ISO date string (YYYY-MM-DD) for backtest end.
        token_ids : list[str]
            Polymarket token IDs to include in the backtest.
        initial_capital_usdc : float
            Starting USDC balance for the simulated account.

        TODO(live): Map Parquet data format to NautilusTrader OrderBook format
        TODO(live): Create proper InstrumentId objects for each token_id
        TODO(live): Configure NautilusTrader venue with correct OMSType
        """
        from nautilus_trader.backtest.engine import BacktestEngine
        from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
        from nautilus_trader.model.enums import AccountType, OmsType
        from nautilus_trader.model.identifiers import TraderId, Venue

        start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)

        log.info(
            "Starting backtest",
            extra={
                "strategy": strategy_class.__name__,
                "start": start_date,
                "end": end_date,
                "token_ids": token_ids,
                "initial_capital": initial_capital_usdc,
            },
        )

        # Initialize engine
        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id=TraderId("BACKTEST-001"),
                logging=LoggingConfig(log_level=self._config.log_level),
            )
        )

        # Add simulated Polymarket venue
        # TODO(live): Configure USDC_POS currency once NautilusTrader version is confirmed
        engine.add_venue(
            venue=Venue("POLYMARKET"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            # starting_balances=[Money(initial_capital_usdc, USDC_POS)],
        )

        # Load data from Parquet catalog
        parquet_files = self._find_data_files(token_ids, start_dt, end_dt)
        if not parquet_files:
            log.warning(
                "No Parquet data found for backtest",
                extra={
                    "token_ids": token_ids,
                    "start": start_date,
                    "end": end_date,
                    "data_dir": str(self._data_dir),
                },
            )
            log.info(
                "Run scripts/download_polymarket_data.py to fetch historical data first."
            )
            return

        for pf in parquet_files:
            log.info("Loading data file", extra={"file": pf.name})
            # TODO(live): engine.add_data(load_parquet_as_orderbook_deltas(pf))

        # Instantiate and add strategy
        strategy = strategy_class(config=self._config)
        engine.add_strategy(strategy)

        log.info("Running backtest engine")
        engine.run()

        # Generate performance reports
        log.info("Backtest complete, generating reports")
        # TODO(live): engine.trader.generate_account_report(Venue("POLYMARKET"))
        # TODO(live): engine.trader.generate_order_fills_report()
        # TODO(live): engine.trader.generate_positions_report()

        engine.dispose()

    def _find_data_files(
        self,
        token_ids: list[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[Path]:
        """
        Find Parquet files for the given tokens and date range.

        Parameters
        ----------
        token_ids : list[str]
            Token IDs to search for.
        start_dt : datetime
            Backtest start.
        end_dt : datetime
            Backtest end.

        Returns
        -------
        list[Path]
            Parquet files covering the requested range.
        """
        files = []
        for token_id in token_ids:
            safe_id = token_id.lstrip("0x")[:32]
            token_dir = self._data_dir / "orderbooks" / safe_id
            if token_dir.exists():
                files.extend(sorted(token_dir.glob("*.parquet")))
        return files
