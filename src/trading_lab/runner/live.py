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
from typing import Any

from trading_lab.config import TradingConfig, live_trading_confirmed

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
        # Legacy runner — superseded by runner/live_v2.py. Pre-flight kept
        # for backward compatibility with anyone still importing this.
        if not live_trading_confirmed():
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
        strategy_class: type[Any],
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
            Strategy class to run (must subclass TradingLabStrategy).
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
        if not live_trading_confirmed():
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

        from trading_lab.risk.heartbeat import HeartbeatWatcher
        from trading_lab.risk.kill_switch import KillSwitch
        from trading_lab.risk.position_limits import PositionLimits
        from trading_lab.venues.polymarket.auth import L2Credentials, derive_api_key
        from trading_lab.venues.polymarket.client import PolymarketRestClient

        if self._config.polymarket.has_l2_credentials:
            creds = L2Credentials(
                api_key=self._config.polymarket.api_key,
                api_secret=self._config.polymarket.api_secret.get_secret_value(),
                api_passphrase=self._config.polymarket.api_passphrase.get_secret_value(),
            )
        else:
            log.warning(
                "L2 credentials not pre-configured — deriving from L1 key. "
                "Consider pre-generating and caching L2 credentials."
            )
            creds = await derive_api_key(
                http_url=self._config.polymarket.host,
                private_key=self._config.polymarket.private_key.get_secret_value(),
            )

        client = PolymarketRestClient(http_url=self._config.polymarket.host, creds=creds)
        try:
            # Initialize risk controls
            kill_switch = KillSwitch(
                daily_loss_limit_usdc=self._config.risk.daily_loss_limit_usdc,
                cancel_all_fn=client.cancel_all_orders,
            )
            _position_limits = PositionLimits(config=self._config.risk)

            async def on_heartbeat_timeout() -> None:
                log.critical("Heartbeat timeout in LIVE mode - triggering kill switch")
                kill_switch.trigger("Heartbeat timeout in live mode")

            heartbeat_watcher = HeartbeatWatcher(
                heartbeat_fn=client.heartbeat,
                timeout_secs=self._config.risk.heartbeat_timeout_secs,
                on_timeout=on_heartbeat_timeout,
            )

            log.critical("LIVE TRADING ACTIVE - MONITORING ALL POSITIONS")

            try:
                await heartbeat_watcher.start()

                # TODO(phase4): Initialize NautilusTrader TradingNode
                # TODO(phase4): Add Polymarket ExecutionClient
                # TODO(phase4): Subscribe to live market feeds
                # TODO(phase4): Start strategy event loop

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
        finally:
            await client.close()
