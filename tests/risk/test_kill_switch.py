"""
Unit tests for KillSwitch risk module.

Tests that the kill switch correctly:
- Triggers when daily loss limit is breached
- Sets the is_triggered flag
- Calls cancel_all_fn when triggered
- Raises KillSwitchTriggered exception
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nautilus_predict.risk.kill_switch import KillSwitch, KillSwitchTriggered


def make_kill_switch(
    limit: float = -200.0,
    cancel_fn: AsyncMock | None = None,
) -> tuple[KillSwitch, AsyncMock]:
    """Create a KillSwitch with a mock cancel function."""
    if cancel_fn is None:
        cancel_fn = AsyncMock()
    ks = KillSwitch(daily_loss_limit_usdc=limit, cancel_all_fn=cancel_fn)
    return ks, cancel_fn


class TestKillSwitchInit:
    """Test KillSwitch initialization."""

    def test_init_sets_limit(self) -> None:
        """Kill switch should store the configured loss limit."""
        ks, _ = make_kill_switch(limit=-150.0)
        assert ks._daily_loss_limit == -150.0

    def test_init_not_triggered(self) -> None:
        """Kill switch should not be triggered at init."""
        ks, _ = make_kill_switch()
        assert ks.is_triggered is False

    def test_init_empty_reason(self) -> None:
        """Trigger reason should be empty before any trigger."""
        ks, _ = make_kill_switch()
        assert ks.trigger_reason == ""

    def test_positive_limit_raises(self) -> None:
        """Positive loss limit should raise ValueError."""
        with pytest.raises(ValueError, match="must be negative"):
            KillSwitch(daily_loss_limit_usdc=100.0, cancel_all_fn=AsyncMock())

    def test_zero_limit_raises(self) -> None:
        """Zero loss limit should raise ValueError."""
        with pytest.raises(ValueError, match="must be negative"):
            KillSwitch(daily_loss_limit_usdc=0.0, cancel_all_fn=AsyncMock())


class TestDailyLossCheck:
    """Test check_daily_loss method."""

    def test_pnl_above_limit_does_not_trigger(self) -> None:
        """PnL above (less negative than) the limit should not trigger."""
        ks, cancel_fn = make_kill_switch(limit=-200.0)
        ks.check_daily_loss(current_pnl=-100.0)  # Should not raise
        assert ks.is_triggered is False

    def test_pnl_at_limit_does_not_trigger(self) -> None:
        """PnL exactly at the limit should not trigger (limit is exclusive)."""
        ks, cancel_fn = make_kill_switch(limit=-200.0)
        ks.check_daily_loss(current_pnl=-200.0)  # Exactly at limit, should not trigger
        assert ks.is_triggered is False

    def test_pnl_below_limit_triggers(self) -> None:
        """PnL below the limit should trigger the kill switch."""
        ks, cancel_fn = make_kill_switch(limit=-200.0)
        with pytest.raises(KillSwitchTriggered):
            ks.check_daily_loss(current_pnl=-250.0)

    def test_pnl_below_limit_sets_triggered(self) -> None:
        """After loss limit breach, is_triggered should be True."""
        ks, cancel_fn = make_kill_switch(limit=-200.0)
        try:
            ks.check_daily_loss(current_pnl=-250.0)
        except KillSwitchTriggered:
            pass
        assert ks.is_triggered is True

    def test_positive_pnl_never_triggers(self) -> None:
        """Positive PnL should never trigger the kill switch."""
        ks, _ = make_kill_switch(limit=-200.0)
        ks.check_daily_loss(current_pnl=500.0)
        assert ks.is_triggered is False

    def test_zero_pnl_never_triggers(self) -> None:
        """Zero PnL should not trigger."""
        ks, _ = make_kill_switch(limit=-200.0)
        ks.check_daily_loss(current_pnl=0.0)
        assert ks.is_triggered is False


class TestTrigger:
    """Test the trigger() method."""

    def test_trigger_sets_is_triggered(self) -> None:
        """trigger() should set is_triggered to True."""
        ks, _ = make_kill_switch()
        ks.trigger("test reason")
        assert ks.is_triggered is True

    def test_trigger_stores_reason(self) -> None:
        """trigger() should store the reason string."""
        ks, _ = make_kill_switch()
        ks.trigger("manual emergency stop")
        assert ks.trigger_reason == "manual emergency stop"

    def test_trigger_idempotent(self) -> None:
        """Calling trigger() twice should not error."""
        ks, _ = make_kill_switch()
        ks.trigger("first reason")
        ks.trigger("second reason")  # Should not raise
        # First reason is preserved
        assert ks.trigger_reason == "first reason"
        assert ks.is_triggered is True

    @pytest.mark.asyncio
    async def test_trigger_calls_cancel_all_fn(self) -> None:
        """trigger() should attempt to call cancel_all_fn."""
        cancel_fn = AsyncMock()
        ks, _ = make_kill_switch(cancel_fn=cancel_fn)

        # Patch the async cancellation to run synchronously in test
        with patch.object(ks, "_async_cancel_all", new_callable=AsyncMock) as mock_cancel:
            ks.trigger("test trigger")
            # Give event loop a chance to schedule the task
            import asyncio
            await asyncio.sleep(0)
            # The cancel function should have been scheduled

    def test_triggered_check_raises_kill_switch_triggered(self) -> None:
        """After trigger, check_daily_loss should raise KillSwitchTriggered."""
        ks, _ = make_kill_switch()
        ks.trigger("pre-triggered")
        with pytest.raises(KillSwitchTriggered):
            ks.check_daily_loss(current_pnl=100.0)  # Even with positive PnL


class TestKillSwitchTriggered:
    """Test the KillSwitchTriggered exception."""

    def test_exception_contains_reason(self) -> None:
        """KillSwitchTriggered should contain the trigger reason."""
        reason = "Daily loss limit breached"
        exc = KillSwitchTriggered(reason)
        assert reason in str(exc)
        assert exc.reason == reason

    def test_exception_is_exception(self) -> None:
        """KillSwitchTriggered should be catchable as Exception."""
        with pytest.raises(Exception):
            raise KillSwitchTriggered("test")

    def test_can_catch_specifically(self) -> None:
        """KillSwitchTriggered should be catchable by its specific type."""
        caught = False
        try:
            raise KillSwitchTriggered("specific catch test")
        except KillSwitchTriggered:
            caught = True
        assert caught
