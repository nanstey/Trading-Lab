"""
Binary Complement Arbitrage Strategy for Polymarket.

In binary prediction markets, YES + NO shares for the same outcome must resolve
to exactly $1.00. If the combined best-ask price for YES and NO is strictly
below $1.00, buying both guarantees a risk-free profit at resolution.

Arbitrage condition
-------------------
    best_ask(YES) + best_ask(NO) < 1.00 - costs

When this condition is met:
1. Submit limit orders to buy YES at best_ask_yes.
2. Submit limit orders to buy NO at best_ask_no.
3. Hold both positions until the market resolves at $1.00.

Notes
-----
- Both orders must execute. This strategy uses paired submission and cancels
  the surviving leg if only one side fills.
- Polymarket charges 0% maker fee; rebate offsets slippage risk.
- Taker fee is 2% on notional, so only execute as a taker if spread > 2%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import structlog
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

log = structlog.get_logger(__name__)

# Taker fee on Polymarket (2 bps = 0.02)
TAKER_FEE = 0.02


class MarketPair(NamedTuple):
    """YES and NO token IDs for a single Polymarket condition."""

    condition_id: str
    yes_instrument_id: InstrumentId
    no_instrument_id: InstrumentId


@dataclass
class ArbOpportunity:
    """Identified arbitrage opportunity awaiting execution."""

    pair: MarketPair
    yes_ask: float
    no_ask: float
    combined_cost: float
    expected_profit: float
    yes_order_id: str | None = None
    no_order_id: str | None = None
    yes_filled: bool = False
    no_filled: bool = False


@dataclass
class BinaryArbConfig(StrategyConfig, frozen=True):
    """Configuration for the binary complement arbitrage strategy."""

    strategy_id: str = "BINARY-ARB-001"
    min_profit_usdc: float = 0.02     # Minimum profit per pair (after fees)
    max_capital_usdc: float = 1000.0  # Maximum capital to deploy per arb


class BinaryArbStrategy(Strategy):
    """
    Scans Polymarket binary markets for complement arbitrage opportunities.

    Requires pairs of YES/NO instruments to be registered via
    register_market_pair() before starting.
    """

    def __init__(self, config: BinaryArbConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._pairs: list[MarketPair] = []
        self._active_arbs: dict[str, ArbOpportunity] = {}   # condition_id → arb
        self._best_asks: dict[str, float] = {}              # instrument_id → price

    def register_market_pair(
        self,
        condition_id: str,
        yes_instrument_id: InstrumentId,
        no_instrument_id: InstrumentId,
    ) -> None:
        """Register a YES/NO instrument pair to scan for arb opportunities."""
        pair = MarketPair(condition_id, yes_instrument_id, no_instrument_id)
        self._pairs.append(pair)
        self.subscribe_order_book_deltas(yes_instrument_id)
        self.subscribe_order_book_deltas(no_instrument_id)
        log.info(
            "Registered market pair",
            condition_id=condition_id,
            yes=str(yes_instrument_id),
            no=str(no_instrument_id),
        )

    def on_start(self) -> None:
        log.info(
            "BinaryArbStrategy started",
            min_profit=self._cfg.min_profit_usdc,
            pairs=len(self._pairs),
        )

    def on_stop(self) -> None:
        log.info("BinaryArbStrategy stopping")
        self.cancel_all_orders()

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        iid = str(deltas.instrument_id)
        book = self.cache.order_book(deltas.instrument_id)
        if book is None or book.best_ask_price() is None:
            return

        self._best_asks[iid] = float(book.best_ask_price())
        self._scan_for_arb(deltas.instrument_id)

    def _scan_for_arb(self, updated_id: InstrumentId) -> None:
        """Check all pairs that include the updated instrument for arb."""
        for pair in self._pairs:
            if updated_id not in (pair.yes_instrument_id, pair.no_instrument_id):
                continue

            # Already have an active arb for this condition
            if pair.condition_id in self._active_arbs:
                continue

            yes_ask = self._best_asks.get(str(pair.yes_instrument_id))
            no_ask = self._best_asks.get(str(pair.no_instrument_id))
            if yes_ask is None or no_ask is None:
                continue

            combined_cost = yes_ask + no_ask
            profit = 1.0 - combined_cost - TAKER_FEE

            if profit >= self._cfg.min_profit_usdc:
                log.info(
                    "Arb opportunity detected",
                    condition_id=pair.condition_id,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    combined_cost=combined_cost,
                    expected_profit=profit,
                )
                self._execute_arb(pair, yes_ask, no_ask, profit)

    def _execute_arb(
        self,
        pair: MarketPair,
        yes_ask: float,
        no_ask: float,
        expected_profit: float,
    ) -> None:
        """Submit paired limit orders to capture the arbitrage."""
        max_shares = self._cfg.max_capital_usdc / (yes_ask + no_ask)
        size = min(max_shares, self._cfg.max_capital_usdc)
        size = round(size, 4)

        yes_order = self.order_factory.limit(
            instrument_id=pair.yes_instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str(f"{size:.4f}"),
            price=Price.from_str(f"{yes_ask:.4f}"),
            time_in_force=TimeInForce.IOC,   # immediate-or-cancel for arb
        )
        no_order = self.order_factory.limit(
            instrument_id=pair.no_instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str(f"{size:.4f}"),
            price=Price.from_str(f"{no_ask:.4f}"),
            time_in_force=TimeInForce.IOC,
        )

        arb = ArbOpportunity(
            pair=pair,
            yes_ask=yes_ask,
            no_ask=no_ask,
            combined_cost=yes_ask + no_ask,
            expected_profit=expected_profit,
            yes_order_id=str(yes_order.client_order_id),
            no_order_id=str(no_order.client_order_id),
        )
        self._active_arbs[pair.condition_id] = arb

        self.submit_order(yes_order)
        self.submit_order(no_order)

        log.info(
            "Arb orders submitted",
            condition_id=pair.condition_id,
            size=size,
            yes_ask=yes_ask,
            no_ask=no_ask,
        )

    def on_order_filled(self, event) -> None:
        order_id = str(event.client_order_id)
        # Find the matching arb
        for cid, arb in list(self._active_arbs.items()):
            if order_id == arb.yes_order_id:
                arb.yes_filled = True
            elif order_id == arb.no_order_id:
                arb.no_filled = True

            if arb.yes_filled and arb.no_filled:
                log.info(
                    "Arb fully filled — holding to resolution",
                    condition_id=cid,
                    combined_cost=arb.combined_cost,
                    expected_profit=arb.expected_profit,
                )
                del self._active_arbs[cid]

    def on_order_canceled(self, event) -> None:
        """If one leg of an arb is cancelled, cancel the other to avoid naked exposure."""
        order_id = str(event.client_order_id)
        for cid, arb in list(self._active_arbs.items()):
            if order_id == arb.yes_order_id and not arb.no_filled:
                log.warning("Arb YES leg cancelled; aborting arb", condition_id=cid)
                self._abort_arb(arb)
                del self._active_arbs[cid]
            elif order_id == arb.no_order_id and not arb.yes_filled:
                log.warning("Arb NO leg cancelled; aborting arb", condition_id=cid)
                self._abort_arb(arb)
                del self._active_arbs[cid]

    def _abort_arb(self, arb: ArbOpportunity) -> None:
        """Cancel any live orders for this arb to avoid naked exposure."""
        if arb.yes_order_id and not arb.yes_filled:
            order = self.cache.order(self.cache.client_order_id(arb.yes_order_id))
            if order and order.is_open:
                self.cancel_order(order)
        if arb.no_order_id and not arb.no_filled:
            order = self.cache.order(self.cache.client_order_id(arb.no_order_id))
            if order and order.is_open:
                self.cancel_order(order)

    def on_reset(self) -> None:
        self._active_arbs.clear()
        self._best_asks.clear()
