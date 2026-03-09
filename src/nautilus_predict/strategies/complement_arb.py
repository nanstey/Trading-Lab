"""
Binary Complement Arbitrage Strategy.

Polymarket binary markets have complementary YES and NO tokens that must
sum to exactly 1.00 USDC at settlement. If market inefficiencies cause:

    YES_ask + NO_ask < 1.00

...then simultaneously buying both YES and NO at those ask prices creates
a risk-free arbitrage: you pay < $1.00 for a position that settles at $1.00.

Example:
    YES ask = 0.60, NO ask = 0.39
    Cost to buy both = 0.99 USDC
    Settlement value = 1.00 USDC
    Profit = 0.01 USDC per pair (risk-free, ignoring gas/fees)

Strategy logic:
1. Subscribe to order books for YES and NO tokens of a binary market.
2. On each update, check if YES_ask + NO_ask < 1.00.
3. If opportunity exists, place simultaneous limit buy orders.
4. Orders are placed at the observed ask price (taker) to guarantee fill.

Note: This is a taker strategy (crosses the spread). Unlike market making,
there is no maker rebate. Minimum profit threshold should account for
any transaction fees and slippage.

TODO(live): Wire to PolymarketClient for order placement
TODO(live): Handle partial fills - track open legs to avoid unhedged exposure
TODO(live): Consider using polyfill-rs for simultaneous order submission
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from nautilus_predict.strategies.base import NautilusPredictStrategy

if TYPE_CHECKING:
    from nautilus_trader.model.data import OrderBookDeltas
    from nautilus_trader.model.events import OrderFilled, PositionChanged

    from nautilus_predict.config import TradingConfig
    from nautilus_predict.risk.kill_switch import KillSwitch

log = logging.getLogger(__name__)

# Minimum profit threshold as a fraction of $1.00 (e.g., 0.005 = 0.5 cents)
DEFAULT_MIN_PROFIT = Decimal("0.005")


class ComplementArbStrategy(NautilusPredictStrategy):
    """
    Risk-free complement arbitrage on Polymarket binary markets.

    Buys both YES and NO tokens when their combined ask price is < 1.00 USDC.

    Parameters
    ----------
    condition_id : str
        Polymarket condition ID for the binary market.
    yes_token_id : str
        Token ID for the YES outcome.
    no_token_id : str
        Token ID for the NO outcome.
    min_profit_usdc : float
        Minimum required profit in USDC to execute arb (default: 0.005).
    max_order_size_usdc : float
        Maximum capital to deploy per arb opportunity (default: 100.0).
    config : TradingConfig
        System configuration.
    kill_switch : KillSwitch, optional
        Risk management kill switch.
    """

    def __init__(
        self,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        min_profit_usdc: float = 0.005,
        max_order_size_usdc: float = 100.0,
        config: TradingConfig | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        if config is None:
            raise ValueError("config must be provided")
        super().__init__(config=config, kill_switch=kill_switch)
        self._condition_id = condition_id
        self._yes_token_id = yes_token_id
        self._no_token_id = no_token_id
        self._min_profit = Decimal(str(min_profit_usdc))
        self._max_size = Decimal(str(max_order_size_usdc))

        # Track best asks from each side's book
        self._yes_best_ask: Decimal | None = None
        self._no_best_ask: Decimal | None = None

    # ------------------------------------------------------------------
    # Strategy lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """Subscribe to order books for both YES and NO tokens."""
        log.info(
            "ComplementArb starting",
            extra={
                "condition_id": self._condition_id,
                "yes_token": self._yes_token_id,
                "no_token": self._no_token_id,
                "min_profit": float(self._min_profit),
            },
        )
        # TODO(live): Subscribe to order book channels for both tokens

    def on_stop(self) -> None:
        """Called when strategy stops."""
        log.info("ComplementArb stopping", extra={"condition_id": self._condition_id})

    # ------------------------------------------------------------------
    # Book update handler
    # ------------------------------------------------------------------

    def on_book_update(self, deltas: OrderBookDeltas) -> None:
        """
        Update best ask for the relevant token and check for arb opportunity.

        Routes to YES or NO state based on instrument ID in the deltas.
        """
        self._check_kill_switch()

        # TODO(live): Determine whether this update is for YES or NO token
        # and update self._yes_best_ask or self._no_best_ask accordingly
        # Example:
        #   if deltas.instrument_id.symbol == self._yes_token_id:
        #       self._yes_best_ask = extract_best_ask(deltas)
        #   elif deltas.instrument_id.symbol == self._no_token_id:
        #       self._no_best_ask = extract_best_ask(deltas)

        # Check arb after both books have been seen
        if self._yes_best_ask is not None and self._no_best_ask is not None:
            if self.check_arb_opportunity(self._yes_best_ask, self._no_best_ask):
                log.info(
                    "Arb opportunity detected",
                    extra={
                        "condition_id": self._condition_id,
                        "yes_ask": float(self._yes_best_ask),
                        "no_ask": float(self._no_best_ask),
                        "combined": float(self._yes_best_ask + self._no_best_ask),
                        "profit": float(Decimal("1.00") - self._yes_best_ask - self._no_best_ask),
                    },
                )
                self.execute_arb(self._condition_id)

    # ------------------------------------------------------------------
    # Fill and position handlers
    # ------------------------------------------------------------------

    def on_fill(self, event: OrderFilled) -> None:
        """
        Track fill state for both legs of the arb.

        If only one leg fills, we need to handle the resulting exposure.
        TODO(live): Implement leg tracking and partial fill handling
        """
        log.info("Arb fill received", extra={"condition_id": self._condition_id})

    def on_position_changed(self, event: PositionChanged) -> None:
        """Update position tracking when arb positions change."""
        log.debug("Position changed", extra={"condition_id": self._condition_id})

    # ------------------------------------------------------------------
    # Core arb logic (public for testability)
    # ------------------------------------------------------------------

    def check_arb_opportunity(self, yes_ask: Decimal, no_ask: Decimal) -> bool:
        """
        Determine if a complement arb opportunity exists.

        An opportunity exists when:
            YES_ask + NO_ask < 1.00 - min_profit

        The min_profit buffer ensures the trade is profitable after
        any fees or slippage.

        Parameters
        ----------
        yes_ask : Decimal
            Current best ask price for the YES token (0.00 to 1.00).
        no_ask : Decimal
            Current best ask price for the NO token (0.00 to 1.00).

        Returns
        -------
        bool
            True if the combined cost is strictly less than 1.00 - min_profit.

        Examples
        --------
        >>> strategy.check_arb_opportunity(Decimal("0.60"), Decimal("0.39"))
        True   # 0.99 < 1.00, profit = 0.01

        >>> strategy.check_arb_opportunity(Decimal("0.55"), Decimal("0.46"))
        False  # 1.01 > 1.00, no profit

        >>> strategy.check_arb_opportunity(Decimal("0.50"), Decimal("0.50"))
        False  # 1.00 == 1.00, no profit (need strictly < 1.00)
        """
        combined_cost = yes_ask + no_ask
        profit = Decimal("1.00") - combined_cost
        return profit > self._min_profit

    def execute_arb(self, market_id: str) -> None:
        """
        Execute a complement arbitrage by buying both YES and NO tokens.

        Places simultaneous limit orders at the observed ask prices.
        Order size is scaled to the minimum of available liquidity at
        each ask level and max_order_size_usdc.

        Parameters
        ----------
        market_id : str
            Condition ID of the market to arb.

        TODO(live): Build and submit two signed limit orders simultaneously
        TODO(live): Use polyfill-rs for low-latency dual order submission
        TODO(live): Track leg status to handle partial fills
        """
        if self._yes_best_ask is None or self._no_best_ask is None:
            log.warning("Cannot execute arb: missing book data", extra={"market_id": market_id})
            return

        profit_per_unit = Decimal("1.00") - self._yes_best_ask - self._no_best_ask
        # Calculate units to buy based on budget and profit
        units = float(self._max_size / (self._yes_best_ask + self._no_best_ask))

        log.info(
            "Executing arb",
            extra={
                "market_id": market_id,
                "yes_ask": float(self._yes_best_ask),
                "no_ask": float(self._no_best_ask),
                "profit_per_unit": float(profit_per_unit),
                "units": units,
                "total_profit_usdc": float(profit_per_unit) * units,
            },
        )

        # TODO(live): Submit YES buy order
        # yes_order = {
        #     "token_id": self._yes_token_id,
        #     "side": "BUY",
        #     "price": str(self._yes_best_ask),
        #     "size": str(units),
        #     "type": "LIMIT",
        # }
        # await self._client.place_order(yes_order)

        # TODO(live): Submit NO buy order
        # no_order = {
        #     "token_id": self._no_token_id,
        #     "side": "BUY",
        #     "price": str(self._no_best_ask),
        #     "size": str(units),
        #     "type": "LIMIT",
        # }
        # await self._client.place_order(no_order)
