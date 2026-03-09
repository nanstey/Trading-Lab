"""
Risk management module for Nautilus-Predict.

Three layers of protection:
1. KillSwitch: Triggers on daily loss breach, halts all trading
2. HeartbeatWatcher: Monitors connectivity, cancels orders on timeout
3. PositionLimits: Enforces per-market and total portfolio exposure caps
"""

from nautilus_predict.risk.heartbeat import HeartbeatWatcher
from nautilus_predict.risk.kill_switch import KillSwitch
from nautilus_predict.risk.position_limits import PositionLimits

__all__ = ["KillSwitch", "HeartbeatWatcher", "PositionLimits"]
