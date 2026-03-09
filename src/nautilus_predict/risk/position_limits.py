"""
Position Limits Risk Module.

Enforces hard limits on:
1. Per-market position size (MAX_POSITION_USDC)
2. Total portfolio exposure (MAX_TOTAL_EXPOSURE_USDC)

These limits prevent a single runaway strategy from causing outsized losses.
All order-submitting code should call these checks before placing orders.
"""

from __future__ import annotations

import logging

from nautilus_predict.config import RiskConfig

log = logging.getLogger(__name__)


class PositionLimitBreached(Exception):
    """
    Raised when a proposed order would breach position limits.

    Contains details about which limit was breached and by how much.
    """

    def __init__(self, message: str, market_id: str | None = None) -> None:
        super().__init__(message)
        self.market_id = market_id


class PositionLimits:
    """
    Enforces per-market and total portfolio position limits.

    Parameters
    ----------
    config : RiskConfig
        Risk configuration with limit thresholds.

    Example
    -------
    >>> limits = PositionLimits(config=risk_config)
    >>> limits.check_position("market_1", order_size_usdc=50.0, current_usdc=60.0)
    >>> limits.check_total_exposure({"market_1": 60.0, "market_2": 80.0})
    """

    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def check_position(
        self,
        market_id: str,
        size_usdc: float,
        current_position_usdc: float,
    ) -> None:
        """
        Check if a new order would breach the per-market position limit.

        Parameters
        ----------
        market_id : str
            Market identifier for logging and error context.
        size_usdc : float
            Size of the proposed new order in USDC.
        current_position_usdc : float
            Current net position in USDC for this market.

        Raises
        ------
        PositionLimitBreached
            If accepting this order would cause the total position to exceed
            MAX_POSITION_USDC.
        """
        proposed_position = abs(current_position_usdc) + size_usdc

        if proposed_position > self._config.max_position_usdc:
            msg = (
                f"Per-market position limit breached for {market_id}: "
                f"proposed {proposed_position:.2f} USDC > limit "
                f"{self._config.max_position_usdc:.2f} USDC "
                f"(current={current_position_usdc:.2f}, order={size_usdc:.2f})"
            )
            log.warning(msg)
            raise PositionLimitBreached(msg, market_id=market_id)

        remaining = self._config.max_position_usdc - proposed_position
        log.debug(
            "Position check passed",
            extra={
                "market_id": market_id,
                "proposed_usdc": proposed_position,
                "limit_usdc": self._config.max_position_usdc,
                "remaining_usdc": remaining,
            },
        )

    def check_total_exposure(self, all_positions: dict[str, float]) -> None:
        """
        Check if total portfolio exposure is within limits.

        Parameters
        ----------
        all_positions : dict[str, float]
            Mapping of market_id to current net position in USDC.
            Values should be signed (positive = long, negative = short).

        Raises
        ------
        PositionLimitBreached
            If total exposure exceeds MAX_TOTAL_EXPOSURE_USDC.
        """
        total_exposure = sum(abs(v) for v in all_positions.values())

        if total_exposure > self._config.max_total_exposure_usdc:
            markets_str = ", ".join(
                f"{k}={v:.2f}" for k, v in sorted(all_positions.items())
            )
            msg = (
                f"Total exposure limit breached: "
                f"{total_exposure:.2f} USDC > limit "
                f"{self._config.max_total_exposure_usdc:.2f} USDC. "
                f"Positions: {markets_str}"
            )
            log.warning(msg)
            raise PositionLimitBreached(msg)

        remaining = self._config.max_total_exposure_usdc - total_exposure
        log.debug(
            "Total exposure check passed",
            extra={
                "total_usdc": total_exposure,
                "limit_usdc": self._config.max_total_exposure_usdc,
                "remaining_usdc": remaining,
                "market_count": len(all_positions),
            },
        )

    def available_capacity(
        self,
        market_id: str,
        current_position_usdc: float,
        all_positions: dict[str, float],
    ) -> float:
        """
        Return the maximum additional USDC that can be deployed.

        Takes the minimum of per-market remaining capacity and total
        remaining portfolio capacity.

        Parameters
        ----------
        market_id : str
            Target market.
        current_position_usdc : float
            Current net position in this market.
        all_positions : dict[str, float]
            All current positions including this market.

        Returns
        -------
        float
            Maximum additional USDC that can be safely deployed.
        """
        per_market_remaining = self._config.max_position_usdc - abs(current_position_usdc)
        total_exposure = sum(abs(v) for v in all_positions.values())
        total_remaining = self._config.max_total_exposure_usdc - total_exposure
        return max(0.0, min(per_market_remaining, total_remaining))
