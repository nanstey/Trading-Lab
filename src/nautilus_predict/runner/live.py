"""
Live Trading Runner.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! WARNING: THIS MODULE EXECUTES REAL ORDERS WITH REAL FUNDS              !!
!! DOUBLE-CHECK ALL CONFIGURATION BEFORE ENABLING LIVE TRADING           !!
!! START WITH SMALL POSITION LIMITS AND MONITOR CLOSELY                  !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

LIVE TRADING REQUIRES EXPLICIT DOUBLE OPT-IN:
    1. Set TRADING_MODE=live in your .env file
    2. Set LIVE_TRADING_CONFIRMED=true in your .env file
    3. Ensure all risk limits are configured appropriately
    4. Verify API credentials are correct
    5. Start with conservative position limits (MAX_POSITION_USDC=10.0)

If LIVE_TRADING_CONFIRMED is not set to "true", this runner will raise
LiveTradingNotEnabled and refuse to start. This is intentional.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Type

from nautilus_predict.config import TradingConfig, TradingMode

log = logging.getLogger(__name__)


class LiveTradingNotEnabled(Exception):
    """
    Raised when live trading is attempted without explicit confirmation.

    To enable live trading:
        1. Set TRADING_MODE=live in your .env
        2. Set LIVE_TRADING_CONFIRMED=true in your .env

    Both must be set simultaneously.
    """


class LiveRunner:
    """
    Live trading runner with real order execution.

    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    !! EXECUTES REAL ORDERS - USE WITH EXTREME CAUTION    !!
    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

    Requires explicit double opt-in:
    - TRADING_MODE=live
    - LIVE_TRADING_CONFIRMED=true

    Connects to live Polymarket and Hyperliquid venues, runs
    strategies with real order submission and fills.

    Parameters
    ----------
    config : TradingConfig
        System configuration. Must have trading_mode == LIVE
        AND LIVE_TRADING_CONFIRMED=true env var.

    Raises
    ------
    LiveTradingNotEnabled
        If TRADING_MODE != live OR LIVE_TRADING_CONFIRMED != true.
    """

    def __init__(self, config: TradingConfig) -> None:
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # CRITICAL SAFETY CHECK - DO NOT REMOVE OR WEAKEN
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        if config.trading_mode != TradingMode.LIVE:
            raise LiveTradingNotEnabled(
                f"LiveRunner requires TRADING_MODE=live, got: {config.trading_mode.value}. "
                "Set TRADING_MODE=live in your .env to enable live trading."
            )

        # TradingConfig already validates LIVE_TRADING_CONFIRMED at construction,
        # but we double-check here as an extra safety layer.
        confirmed = os.environ.get("LIVE_TRADING_CONFIRMED", "").lower()
        if confirmed != "true":
            raise LiveTradingNotEnabled(
                "Live trading requires LIVE_TRADING_CONFIRMED=true in environment. "
                "This is a safety guard to prevent accidental live trading.\n"
                "\n"
                "To enable live trading:\n"
                "  1. Set TRADING_MODE=live in your .env\n"
                "  2. Set LIVE_TRADING_CONFIRMED=true in your .env\n"
                "  3. Verify all risk limits are set conservatively\n"
                "  4. Ensure API credentials are valid\n"
            )

        self._config = config
        log.warning(
            "LiveRunner initialized - REAL MONEY TRADING MODE ACTIVE",
            extra={
                "polymarket_address": "configured" if config.polymarket.has_l1_credentials else "MISSING",
                "risk_daily_loss_limit": config.risk.daily_loss_limit_usdc,
                "risk_max_position": config.risk.max_position_usdc,
                "risk_max_total_exposure": config.risk.max_total_exposure_usdc,
            },
        )

    async def run(
        self,
        strategy_class: Type[Any],
        token_ids: list[str],
    ) -> None:
        """
        Start live trading.

        !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        !! THIS PLACES REAL ORDERS WITH REAL USDC COLLATERAL  !!
        !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        Parameters
        ----------
        strategy_class : type
            Strategy class to run (must subclass NautilusPredictStrategy).
        token_ids : list[str]
            Polymarket token IDs to trade.

        Raises
        ------
        LiveTradingNotEnabled
            As a final safety check at the start of run().

        TODO(live): Integrate NautilusTrader TradingNode for live execution
        TODO(live): Configure Polymarket ExecutionClient
        TODO(live): Set up monitoring and alerting
        TODO(live): Implement graceful shutdown with position unwinding
        """
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # FINAL SAFETY CHECK at run() entry point
        # This catches any code path that bypasses __init__
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        confirmed = os.environ.get("LIVE_TRADING_CONFIRMED", "").lower()
        if confirmed != "true":
            raise LiveTradingNotEnabled(
                "LIVE_TRADING_CONFIRMED env var not set to 'true'. "
                "Aborting live trading."
            )

        log.critical(
            "LIVE TRADING STARTING",
            extra={
                "strategy": strategy_class.__name__,
                "token_count": len(token_ids),
                "daily_loss_limit": self._config.risk.daily_loss_limit_usdc,
                "max_position_usdc": self._config.risk.max_position_usdc,
            },
        )

        from nautilus_predict.adapters.polymarket.auth import PolymarketAuth, L2Credentials
        from nautilus_predict.adapters.polymarket.client import PolymarketClient
        from nautilus_predict.risk.kill_switch import KillSwitch
        from nautilus_predict.risk.heartbeat import HeartbeatWatcher
        from nautilus_predict.risk.position_limits import PositionLimits

        auth = PolymarketAuth(
            private_key=self._config.polymarket.private_key.get_secret_value()
        )

        # Configure L2 credentials if pre-generated
        if self._config.polymarket.has_l2_credentials:
            auth.set_l2_credentials(L2Credentials(
                api_key=self._config.polymarket.api_key,
                api_secret=self._config.polymarket.api_secret.get_secret_value(),
                api_passphrase=self._config.polymarket.api_passphrase.get_secret_value(),
            ))
        else:
            log.warning(
                "L2 credentials not pre-configured. "
                "Will attempt to derive from L1 key. "
                "Consider pre-generating and caching L2 credentials."
            )

        async with PolymarketClient(config=self._config.polymarket, auth=auth) as client:
            # Derive L2 credentials if needed
            if not self._config.polymarket.has_l2_credentials:
                log.info("Deriving L2 credentials from L1 key")
                await auth.derive_l2_credentials(host=self._config.polymarket.host)

            # Initialize risk controls
            kill_switch = KillSwitch(
                daily_loss_limit_usdc=self._config.risk.daily_loss_limit_usdc,
                cancel_all_fn=client.cancel_all_orders,
            )
            position_limits = PositionLimits(config=self._config.risk)

            async def on_heartbeat_timeout() -> None:
                log.critical("Heartbeat timeout in LIVE mode - triggering kill switch")
                kill_switch.trigger("Heartbeat timeout in live mode")

            heartbeat_watcher = HeartbeatWatcher(
                heartbeat_fn=client.heartbeat,
                timeout_secs=self._config.risk.heartbeat_timeout_secs,
                on_timeout=on_heartbeat_timeout,
            )

            # Instantiate strategy
            strategy = strategy_class(
                config=self._config,
                kill_switch=kill_switch,
            )

            log.critical("LIVE TRADING ACTIVE - MONITORING ALL POSITIONS")

            try:
                await heartbeat_watcher.start()

                # TODO(live): Initialize NautilusTrader TradingNode
                # TODO(live): Add Polymarket ExecutionClient
                # TODO(live): Subscribe to live market feeds
                # TODO(live): Start strategy event loop

                # Placeholder: keep running until cancelled
                await asyncio.sleep(float("inf"))

            except (KeyboardInterrupt, asyncio.CancelledError):
                log.critical("LIVE TRADING SHUTDOWN REQUESTED")
            except Exception as exc:
                log.critical(
                    "LIVE TRADING ERROR - TRIGGERING KILL SWITCH",
                    extra={"error": str(exc)},
                )
                kill_switch.trigger(f"Unexpected error: {exc}")
                raise
            finally:
                await heartbeat_watcher.stop()
                log.critical("LIVE TRADING STOPPED")
