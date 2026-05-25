"""
Paper Trading Runner.

Connects to live Polymarket and Hyperliquid WebSocket feeds but
simulates order fills locally. No real orders are placed on any venue.

This mode is used to:
1. Validate strategy logic against live market conditions
2. Measure latency from signal to simulated fill
3. Sanity-check risk module integration before going live

NautilusTrader handles the paper trading simulation:
- Fills are simulated at the market price when orders would cross
- Slippage models can be configured
- Fill reports are generated identically to live mode

Safety: This runner asserts config.mode == TradingMode.PAPER before
running. It will refuse to start if mode is set to live.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nautilus_predict.config import TradingConfig, TradingMode

log = logging.getLogger(__name__)


class PaperRunner:
    """
    Paper trading runner using live market data feeds.

    Connects to live WebSocket feeds but simulates all order execution
    locally via NautilusTrader's paper trading engine.

    Parameters
    ----------
    config : TradingConfig
        System configuration. Must have trading_mode == PAPER.

    Raises
    ------
    AssertionError
        If config.trading_mode is not PAPER.

    Example
    -------
    >>> runner = PaperRunner(config=TradingConfig())
    >>> await runner.run(
    ...     strategy_class=BinaryArbStrategy,
    ...     token_ids=["0xabc..."],
    ... )
    """

    def __init__(self, config: TradingConfig) -> None:
        assert config.trading_mode == TradingMode.PAPER, (
            f"PaperRunner requires TRADING_MODE=paper, got: {config.trading_mode.value}. "
            "Use LiveRunner for live trading."
        )
        self._config = config

    async def run(
        self,
        strategy_class: type[Any],
        token_ids: list[str],
    ) -> None:
        """
        Start paper trading with live market data feeds.

        Connects to Polymarket and Hyperliquid WebSocket feeds, runs
        the strategy with simulated order execution, and logs performance.

        Parameters
        ----------
        strategy_class : type
            Strategy class to run (must subclass NautilusPredictStrategy).
        token_ids : list[str]
            Polymarket token IDs to subscribe to.

        TODO(phase3): Integrate NautilusTrader TradingNode with paper trading config
        TODO(phase3): Configure simulated fills via NautilusTrader's SimulatedExchange
        TODO(phase3): Add performance metrics collection
        """
        log.info(
            "Starting paper trading",
            extra={
                "strategy": strategy_class.__name__,
                "token_count": len(token_ids),
                "mode": self._config.trading_mode.value,
            },
        )

        # Initialize risk module
        from nautilus_predict.risk.heartbeat import HeartbeatWatcher
        from nautilus_predict.risk.kill_switch import KillSwitch
        from nautilus_predict.risk.position_limits import PositionLimits
        from nautilus_predict.venues.polymarket.auth import L2Credentials
        from nautilus_predict.venues.polymarket.client import PolymarketRestClient

        # In paper mode, cancel_all_fn is a no-op (no real orders to cancel)
        async def paper_cancel_all() -> None:
            log.info("Paper mode: simulated cancel-all")

        kill_switch = KillSwitch(
            daily_loss_limit_usdc=self._config.risk.daily_loss_limit_usdc,
            cancel_all_fn=paper_cancel_all,
        )
        _position_limits = PositionLimits(config=self._config.risk)

        creds = L2Credentials(
            api_key=self._config.polymarket.api_key,
            api_secret=self._config.polymarket.api_secret.get_secret_value(),
            api_passphrase=self._config.polymarket.api_passphrase.get_secret_value(),
        )
        client = PolymarketRestClient(http_url=self._config.polymarket.host, creds=creds)
        try:
            # Set up heartbeat watcher
            async def on_heartbeat_timeout() -> None:
                log.error("Heartbeat timeout in paper mode")
                kill_switch.trigger("Heartbeat timeout")

            heartbeat_watcher = HeartbeatWatcher(
                heartbeat_fn=client.heartbeat,
                timeout_secs=self._config.risk.heartbeat_timeout_secs,
                on_timeout=on_heartbeat_timeout,
            )

            log.info("Paper trading initialized, starting feeds")

            try:
                await heartbeat_watcher.start()

                # TODO(phase3): Start NautilusTrader TradingNode with paper config
                # TODO(phase3): Subscribe to market data feeds for all token_ids
                # TODO(phase3): Run strategy event loop

                # Placeholder: keep running until cancelled
                log.info(
                    "Paper trading active - waiting for market data",
                    extra={"token_ids": token_ids[:3]},
                )
                await asyncio.sleep(float("inf"))

            except (KeyboardInterrupt, asyncio.CancelledError):
                log.info("Paper trading shutdown requested")
            finally:
                await heartbeat_watcher.stop()
                log.info("Paper trading stopped")
        finally:
            await client.close()
