"""
Risk management module for Trading Lab.

Three layers of protection:
1. KillSwitch: Triggers on daily loss breach, halts all trading
2. HeartbeatWatcher: Monitors connectivity, cancels orders on timeout
3. PositionLimits: Enforces per-market and total portfolio exposure caps
"""

from trading_lab.risk.heartbeat import HeartbeatWatcher
from trading_lab.risk.kill_switch import KillSwitch
from trading_lab.risk.position_limits import PositionLimits

__all__ = ["KillSwitch", "HeartbeatWatcher", "PositionLimits"]
