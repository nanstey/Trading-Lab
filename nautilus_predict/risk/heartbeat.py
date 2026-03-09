"""
Heartbeat Watcher Risk Module.

Monitors connectivity to the Polymarket CLOB by polling the heartbeat
endpoint. If the heartbeat fails for longer than timeout_secs, all open
orders are cancelled to prevent stale quotes from being filled.

Why this matters:
    If the connection to Polymarket is lost, our quotes remain on the book.
    An adversary can fill our stale quotes at unfavorable prices.
    The heartbeat watcher ensures we cancel all orders if we can't
    confirm the connection is alive.

Behavior:
    - Polls heartbeat_fn every (timeout_secs / 2) seconds
    - If heartbeat fails, increments consecutive failure counter
    - After 2+ consecutive failures, calls on_timeout callback
    - on_timeout should: trigger kill switch + cancel all orders

Design: The polling interval is half the timeout to give two chances
before triggering. This balances sensitivity vs. false positives.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

log = logging.getLogger(__name__)


class HeartbeatWatcher:
    """
    Async background task that monitors system connectivity.

    Polls a heartbeat function and calls an emergency handler if
    connectivity is lost for longer than the timeout period.

    Parameters
    ----------
    heartbeat_fn : callable
        Async function that returns True if the connection is healthy.
        Should call the Polymarket heartbeat REST endpoint.
    timeout_secs : int
        Number of seconds of failure before triggering on_timeout.
        Poll interval is timeout_secs / 2.
    on_timeout : callable
        Async function called when heartbeat times out.
        Should trigger kill switch and cancel all open orders.

    Example
    -------
    >>> watcher = HeartbeatWatcher(
    ...     heartbeat_fn=client.heartbeat,
    ...     timeout_secs=10,
    ...     on_timeout=emergency_shutdown,
    ... )
    >>> await watcher.start()
    >>> # ... trading runs ...
    >>> await watcher.stop()
    """

    def __init__(
        self,
        heartbeat_fn: Callable[[], Awaitable[bool]],
        timeout_secs: int,
        on_timeout: Callable[[], Awaitable[None]],
    ) -> None:
        if timeout_secs <= 0:
            raise ValueError(f"timeout_secs must be positive, got {timeout_secs}")
        self._heartbeat_fn = heartbeat_fn
        self._timeout_secs = timeout_secs
        self._on_timeout = on_timeout
        self._poll_interval = timeout_secs / 2
        self._task: asyncio.Task | None = None
        self._running = False
        self._consecutive_failures = 0

    @property
    def is_running(self) -> bool:
        """Return True if the heartbeat watcher is active."""
        return self._running

    async def start(self) -> None:
        """
        Start the background heartbeat polling task.

        Creates an asyncio task that runs until stop() is called or
        the heartbeat timeout is reached.

        Raises
        ------
        RuntimeError
            If the watcher is already running.
        """
        if self._running:
            raise RuntimeError("HeartbeatWatcher is already running")

        self._running = True
        self._consecutive_failures = 0
        self._task = asyncio.create_task(self._poll_loop(), name="heartbeat-watcher")
        log.info(
            "Heartbeat watcher started",
            extra={
                "timeout_secs": self._timeout_secs,
                "poll_interval_secs": self._poll_interval,
            },
        )

    async def stop(self) -> None:
        """
        Stop the heartbeat polling task gracefully.

        Cancels the background task and waits for it to finish.
        """
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        log.info("Heartbeat watcher stopped")

    async def _poll_loop(self) -> None:
        """
        Background polling loop.

        Polls heartbeat_fn every poll_interval seconds. Triggers
        on_timeout after consecutive failures.
        """
        while self._running:
            await asyncio.sleep(self._poll_interval)

            if not self._running:
                break

            try:
                is_alive = await self._heartbeat_fn()
                if is_alive:
                    if self._consecutive_failures > 0:
                        log.info(
                            "Heartbeat recovered",
                            extra={"previous_failures": self._consecutive_failures},
                        )
                    self._consecutive_failures = 0
                else:
                    await self._handle_failure("Heartbeat returned False")
            except Exception as exc:
                await self._handle_failure(f"Heartbeat exception: {exc}")

    async def _handle_failure(self, reason: str) -> None:
        """
        Handle a single heartbeat failure.

        Increments failure counter and triggers on_timeout after
        the threshold is reached.

        Parameters
        ----------
        reason : str
            Description of why the heartbeat failed.
        """
        self._consecutive_failures += 1
        log.warning(
            "Heartbeat failure",
            extra={
                "reason": reason,
                "consecutive_failures": self._consecutive_failures,
                "timeout_secs": self._timeout_secs,
            },
        )

        # Two consecutive failures = timeout_secs of downtime
        if self._consecutive_failures >= 2:
            log.error(
                "Heartbeat timeout - triggering emergency shutdown",
                extra={
                    "consecutive_failures": self._consecutive_failures,
                    "timeout_secs": self._timeout_secs,
                },
            )
            self._running = False
            try:
                await self._on_timeout()
            except Exception as exc:
                log.critical(
                    "on_timeout callback failed during heartbeat timeout",
                    extra={"error": str(exc)},
                )
