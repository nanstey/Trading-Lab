"""
Abstract base strategy for Trading Lab.

All strategies in this system extend NautilusPredictStrategy, which adds:
- Kill switch integration (halt trading on risk breach)
- Live/paper mode detection
- Standardized lifecycle hooks for book updates, fills, and position changes
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from nautilus_trader.trading.strategy import Strategy

if TYPE_CHECKING:
    from nautilus_trader.model.data import OrderBookDeltas
    from nautilus_trader.model.events import OrderFilled, PositionChanged

    from nautilus_predict.config import TradingConfig
    from nautilus_predict.risk.kill_switch import KillSwitch


class NautilusPredictStrategy(Strategy):
    """
    Abstract base class for all Trading Lab strategies.

    Extends NautilusTrader's Strategy with:
    - Risk / kill-switch integration
    - Mode-aware helpers (is_live_mode, is_paper_mode)
    - Enforced abstract hooks: on_book_update, on_fill, on_position_changed

    Subclasses must implement all abstract methods and call
    super().__init__(config) in their constructors.
    """

    def __init__(self, config: TradingConfig, kill_switch: KillSwitch | None = None) -> None:
        """
        Initialize base strategy.

        Parameters
        ----------
        config : TradingConfig
            System configuration loaded from environment.
        kill_switch : KillSwitch, optional
            Risk kill switch instance. If None, kill switch checks are skipped.
        """
        super().__init__()
        self._config = config
        self._kill_switch = kill_switch

    @property
    def trading_config(self) -> TradingConfig:
        """Return the system trading configuration."""
        return self._config

    # Paper-vs-live is per-strategy now (hypothesis state), not system-wide.
    # The old `trading_mode` / `is_live_mode` / `is_paper_mode` properties
    # were removed — strategies shouldn't make decisions based on mode
    # anyway; that's the runner's job. If a strategy needs to know its
    # lifecycle state, it should query the experiment DB via the slug.

    def _check_kill_switch(self) -> None:
        """
        Check if the kill switch has been triggered.

        Call this at the start of any order-submitting code path.
        Raises RuntimeError if kill switch is active.
        """
        if self._kill_switch is not None and self._kill_switch.is_triggered:
            raise RuntimeError(
                f"Kill switch is active (reason: {self._kill_switch.trigger_reason}). "
                "All order submission is halted. Restart the system to resume."
            )

    @abstractmethod
    def on_book_update(self, deltas: OrderBookDeltas) -> None:
        """
        Called when an order book update is received.

        Subclasses should update internal state and recalculate quotes here.

        Parameters
        ----------
        deltas : OrderBookDeltas
            Order book changes from the venue.
        """
        ...

    @abstractmethod
    def on_fill(self, event: OrderFilled) -> None:
        """
        Called when an order fill event is received.

        Subclasses should update inventory tracking and risk metrics here.

        Parameters
        ----------
        event : OrderFilled
            Fill event from the venue.
        """
        ...

    @abstractmethod
    def on_position_changed(self, event: PositionChanged) -> None:
        """
        Called when position state changes.

        Subclasses should check position limits and adjust quotes here.

        Parameters
        ----------
        event : PositionChanged
            Position change event.
        """
        ...
