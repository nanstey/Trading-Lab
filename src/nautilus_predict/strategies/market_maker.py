"""
Polymarket Market Maker Strategy.

Quotes both sides of the Polymarket CLOB (YES and NO tokens) to earn:
- Zero maker fees: Polymarket charges no fees for passive (maker) orders.
- USDC rebates: Passive liquidity providers receive USDC rebates per fill.

Strategy logic:
1. Subscribe to order book for a target market (token_id).
2. On each book update, calculate a fair value from mid-price.
3. Place a BID `spread_bps/2` below fair and an ASK `spread_bps/2` above fair.
4. Cancel/replace quotes when the book moves more than `requote_threshold_bps`.
5. Respect max_inventory_pct: reduce quote size as inventory accumulates.

Fee-aware quoting:
  All orders must include `feeRateBps` from GET /neg-risk to be valid.
  See: https://docs.polymarket.com/#fees

TODO(live): Wire order placement to PolymarketClient.place_order()
TODO(live): Implement cancel/replace via polyfill-rs hot path for <100ms latency
TODO(live): Subscribe to user fills channel to update inventory in real time
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from nautilus_predict.strategies.base import NautilusPredictStrategy

if TYPE_CHECKING:
    from nautilus_trader.model.data import OrderBookDeltas
    from nautilus_trader.model.events import OrderFilled, PositionChanged

    from nautilus_predict.config import TradingConfig
    from nautilus_predict.risk.kill_switch import KillSwitch

log = logging.getLogger(__name__)


@dataclass
class MarketMakerParams:
    """Tunable parameters for the market maker strategy."""

    spread_bps: int = 50
    """Minimum quoted spread in basis points (e.g. 50 = 0.50 cents on a $1 binary)."""

    order_size_usdc: float = 10.0
    """Base order size in USDC per side."""

    max_inventory_pct: float = 0.20
    """Stop quoting when net inventory exceeds this fraction of max_position_usdc."""

    max_position_usdc: float = 500.0
    """Maximum net position in USDC for this market."""

    requote_threshold_bps: int = 5
    """Requote if fair value has moved more than this many bps since last quote."""

    fee_rate_bps: int = 0
    """Maker fee rate in bps. Zero for most Polymarket markets (maker rebate model)."""


@dataclass
class QuoteState:
    """Internal state tracking for outstanding quotes."""

    bid_order_id: str | None = None
    ask_order_id: str | None = None
    last_fair_value: Decimal = field(default_factory=lambda: Decimal("0"))
    net_inventory_usdc: float = 0.0


class PolymarketMarketMaker(NautilusPredictStrategy):
    """
    Passive market-making strategy for Polymarket binary outcome markets.

    Quotes both sides of the CLOB with a configurable spread.
    Earns USDC rebates on fills. Zero maker fee model.

    Parameters
    ----------
    token_id : str
        Polymarket outcome token ID (YES or NO side of a market).
    params : MarketMakerParams
        Strategy tuning parameters.
    config : TradingConfig
        System configuration.
    kill_switch : KillSwitch, optional
        Risk management kill switch.
    """

    def __init__(
        self,
        token_id: str,
        params: MarketMakerParams,
        config: TradingConfig,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        super().__init__(config=config, kill_switch=kill_switch)
        self._token_id = token_id
        self._params = params
        self._state = QuoteState()

    # ------------------------------------------------------------------
    # Strategy lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """Called by NautilusTrader when strategy starts."""
        log.info(
            "MarketMaker starting",
            extra={
                "token_id": self._token_id,
                "spread_bps": self._params.spread_bps,
                "order_size_usdc": self._params.order_size_usdc,
                "mode": self.trading_mode.value,
            },
        )
        # TODO(live): Subscribe to order book channel for self._token_id

    def on_stop(self) -> None:
        """Called by NautilusTrader when strategy stops. Cancel all open quotes."""
        log.info("MarketMaker stopping, cancelling open quotes")
        self._cancel_all_quotes()

    # ------------------------------------------------------------------
    # Book update handler
    # ------------------------------------------------------------------

    def on_book_update(self, deltas: OrderBookDeltas) -> None:
        """
        Recalculate and refresh quotes when the order book changes.

        Skips requote if fair value has not moved beyond requote_threshold_bps.
        Checks kill switch before submitting any orders.
        """
        self._check_kill_switch()

        fair_value = self._calculate_fair_value(deltas)
        if fair_value is None:
            return

        # Check if requote is warranted
        if not self._should_requote(fair_value):
            return

        bid_price, ask_price = self._calculate_quotes(fair_value)
        order_size = self._calculate_order_size()

        log.debug(
            "Requoting",
            extra={
                "token_id": self._token_id,
                "fair": float(fair_value),
                "bid": float(bid_price),
                "ask": float(ask_price),
                "size_usdc": order_size,
            },
        )

        self._cancel_all_quotes()
        self._submit_bid(bid_price, order_size)
        self._submit_ask(ask_price, order_size)
        self._state.last_fair_value = fair_value

    # ------------------------------------------------------------------
    # Fill and position handlers
    # ------------------------------------------------------------------

    def on_fill(self, event: OrderFilled) -> None:
        """Update inventory when a fill is received."""
        # TODO(live): Determine fill direction (bid vs ask) and update inventory
        log.info(
            "Fill received",
            extra={"order_id": str(event.order_side), "token_id": self._token_id},
        )

    def on_position_changed(self, event: PositionChanged) -> None:
        """Adjust quoting behavior when position limits are approached."""
        # TODO(live): Update self._state.net_inventory_usdc from event
        log.debug("Position changed", extra={"token_id": self._token_id})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _calculate_fair_value(self, deltas: OrderBookDeltas) -> Decimal | None:
        """
        Estimate fair value from the order book mid-price.

        Returns None if the book is too thin to quote reliably.
        """
        # TODO(live): Extract best bid and ask from deltas, compute mid
        # Placeholder: return mid-price of 0.50 (even market)
        return Decimal("0.50")

    def _should_requote(self, new_fair: Decimal) -> bool:
        """Return True if fair value has moved enough to warrant a requote."""
        if self._state.last_fair_value == Decimal("0"):
            return True
        move_bps = abs(new_fair - self._state.last_fair_value) / self._state.last_fair_value * 10000
        return float(move_bps) >= self._params.requote_threshold_bps

    def _calculate_quotes(self, fair_value: Decimal) -> tuple[Decimal, Decimal]:
        """
        Calculate bid and ask prices given a fair value and spread.

        Prices are clamped to [0.01, 0.99] for binary markets.
        """
        half_spread = Decimal(str(self._params.spread_bps)) / Decimal("20000")
        bid = max(Decimal("0.01"), fair_value - half_spread)
        ask = min(Decimal("0.99"), fair_value + half_spread)
        return bid, ask

    def _calculate_order_size(self) -> float:
        """
        Calculate order size in USDC, reduced when inventory is elevated.

        Scales down linearly as inventory approaches max_inventory_pct threshold.
        """
        inventory_ratio = abs(self._state.net_inventory_usdc) / self._params.max_position_usdc
        scale = max(0.0, 1.0 - inventory_ratio / self._params.max_inventory_pct)
        return self._params.order_size_usdc * scale

    def _get_fee_rate_bps(self) -> int:
        """
        Fetch the current maker fee rate for this token.

        Polymarket requires feeRateBps in every order payload.
        Zero for standard markets with maker rebates.

        TODO(live): Call GET /neg-risk/{token_id} to get live feeRateBps
        """
        return self._params.fee_rate_bps

    def _submit_bid(self, price: Decimal, size_usdc: float) -> None:
        """
        Submit a passive BID order to the CLOB.

        TODO(live): Construct signed order payload and call client.place_order()
        """
        fee_bps = self._get_fee_rate_bps()
        log.debug(
            "Submitting bid",
            extra={"price": float(price), "size_usdc": size_usdc, "fee_bps": fee_bps},
        )
        # TODO(live): order = build_order("BID", price, size_usdc, fee_bps)
        # TODO(live): await self._client.place_order(order)

    def _submit_ask(self, price: Decimal, size_usdc: float) -> None:
        """
        Submit a passive ASK order to the CLOB.

        TODO(live): Construct signed order payload and call client.place_order()
        """
        fee_bps = self._get_fee_rate_bps()
        log.debug(
            "Submitting ask",
            extra={"price": float(price), "size_usdc": size_usdc, "fee_bps": fee_bps},
        )
        # TODO(live): order = build_order("ASK", price, size_usdc, fee_bps)
        # TODO(live): await self._client.place_order(order)

    def _cancel_all_quotes(self) -> None:
        """
        Cancel all outstanding quotes for this market.

        TODO(live): Call client.cancel_order() for each open order_id
        """
        if self._state.bid_order_id:
            log.debug("Cancelling bid", extra={"order_id": self._state.bid_order_id})
            # TODO(live): await self._client.cancel_order(self._state.bid_order_id)
            self._state.bid_order_id = None

        if self._state.ask_order_id:
            log.debug("Cancelling ask", extra={"order_id": self._state.ask_order_id})
            # TODO(live): await self._client.cancel_order(self._state.ask_order_id)
            self._state.ask_order_id = None
