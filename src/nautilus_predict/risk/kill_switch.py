"""
Kill Switch Risk Module.

The kill switch is the last line of defense against catastrophic losses.
It permanently halts all trading when triggered and requires a manual
restart to resume operations.

Trigger conditions:
- Daily PnL drops below the configured loss limit (e.g., -$200)
- Manual trigger via trigger() method (e.g., from external monitoring)
- Heartbeat timeout (via HeartbeatWatcher calling trigger())

Effects when triggered:
1. Sets is_triggered flag permanently (until restart)
2. Calls cancel_all_fn to cancel all open orders
3. Logs a critical alert with reason
4. All subsequent order submission raises KillSwitchTriggered

Design principle: FAIL SAFE. Once triggered, only a manual restart
can resume trading. This prevents automated recovery from masking
the underlying issue.
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

log = logging.getLogger(__name__)


class KillSwitchTriggered(Exception):
    """
    Raised when an operation is attempted after the kill switch is active.

    Strategies should catch this and immediately stop all order activity.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Kill switch is active: {reason}")
        self.reason = reason


class KillSwitch:
    """
    Emergency trading halt mechanism.

    Monitors daily PnL and can be triggered manually. Once triggered,
    cancels all open orders and prevents any new orders from being placed.

    Parameters
    ----------
    daily_loss_limit_usdc : float
        Negative threshold (e.g., -200.0). Kill switch triggers when
        current_daily_pnl < daily_loss_limit_usdc.
    cancel_all_fn : callable
        Async function to cancel all open orders across all venues.
        Called immediately when kill switch triggers.

    Example
    -------
    >>> kill_switch = KillSwitch(
    ...     daily_loss_limit_usdc=-200.0,
    ...     cancel_all_fn=client.cancel_all_orders,
    ... )
    >>> kill_switch.check_daily_loss(current_pnl=-150.0)  # OK
    >>> kill_switch.check_daily_loss(current_pnl=-250.0)  # Triggers!
    """

    def __init__(
        self,
        daily_loss_limit_usdc: float,
        cancel_all_fn: Callable[[], Awaitable[None]] | Callable[[], None],
    ) -> None:
        if daily_loss_limit_usdc >= 0:
            raise ValueError(
                f"daily_loss_limit_usdc must be negative, got {daily_loss_limit_usdc}"
            )
        self._daily_loss_limit = daily_loss_limit_usdc
        self._cancel_all_fn = cancel_all_fn
        self._triggered = False
        self._trigger_reason: str = ""

    @property
    def is_triggered(self) -> bool:
        """Return True if the kill switch has been triggered."""
        return self._triggered

    @property
    def trigger_reason(self) -> str:
        """Return the reason the kill switch was triggered, or empty string."""
        return self._trigger_reason

    def check_daily_loss(self, current_pnl: float) -> None:
        """
        Check if daily loss limit has been breached.

        Call this on every fill or PnL update.

        Parameters
        ----------
        current_pnl : float
            Current cumulative daily PnL in USDC (negative = loss).

        Raises
        ------
        KillSwitchTriggered
            If the loss limit has been breached. The kill switch will also
            have already cancelled all open orders by this point.
        """
        if self._triggered:
            raise KillSwitchTriggered(self._trigger_reason)

        if current_pnl < self._daily_loss_limit:
            reason = (
                f"Daily loss limit breached: current PnL {current_pnl:.2f} USDC "
                f"< limit {self._daily_loss_limit:.2f} USDC"
            )
            self.trigger(reason)
            raise KillSwitchTriggered(reason)

    def trigger(self, reason: str) -> None:
        """
        Manually trigger the kill switch.

        Sets the triggered flag, cancels all orders, and logs a critical alert.
        This operation is idempotent - calling multiple times is safe.

        Parameters
        ----------
        reason : str
            Human-readable explanation for why the kill switch was triggered.
        """
        if self._triggered:
            # Already triggered, nothing to do
            return

        self._triggered = True
        self._trigger_reason = reason

        log.critical(
            "KILL SWITCH TRIGGERED - ALL TRADING HALTED",
            extra={
                "reason": reason,
                "daily_loss_limit": self._daily_loss_limit,
            },
        )

        # Attempt to cancel all open orders
        # We call this synchronously via a best-effort approach
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the cancellation as a task
                loop.create_task(self._async_cancel_all())
            else:
                loop.run_until_complete(self._async_cancel_all())
        except RuntimeError:
            # No event loop available - try calling directly
            import inspect
            if inspect.iscoroutinefunction(self._cancel_all_fn):
                log.error("Cannot cancel orders: no event loop available")
            else:
                try:
                    self._cancel_all_fn()
                except Exception as exc:
                    log.error("Failed to cancel orders after kill switch", extra={"error": str(exc)})

    async def _async_cancel_all(self) -> None:
        """Attempt to cancel all open orders asynchronously."""
        import inspect
        try:
            if inspect.iscoroutinefunction(self._cancel_all_fn):
                await self._cancel_all_fn()  # type: ignore[call-arg]
            else:
                self._cancel_all_fn()
            log.critical("All orders successfully cancelled after kill switch")
        except Exception as exc:
            log.error(
                "CRITICAL: Failed to cancel orders after kill switch",
                extra={"error": str(exc)},
            )
