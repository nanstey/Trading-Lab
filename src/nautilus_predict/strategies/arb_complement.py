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
from nautilus_trader.model.enums import BookAction, OrderSide, TimeInForce
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


class BinaryArbConfig(StrategyConfig, frozen=True):
    """Configuration for the binary complement arbitrage strategy."""

    strategy_id: str = "BINARY-ARB-001"
    min_profit_usdc: float = 0.02
    max_capital_usdc: float = 1000.0
    # Per-arb sizing (USDC notional, applied to each leg).
    # Default 5 matches Polymarket's per-order minimum.
    order_notional_usdc: float = 5.0
    # If True, allow the strategy to attempt a new arb on the same condition
    # immediately after submission (don't wait for fill/cancel events). Useful
    # in backtests / IOC flow where events may not arrive promptly.
    allow_concurrent: bool = True
    # Polymarket switched most binary markets to zero-fee taker; override
    # the legacy TAKER_FEE constant via config so it remains tunable.
    taker_fee: float = 0.0


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
        # Set by callers (e.g., BacktestRunner) before on_start; consumed in on_start.
        self._pending_pairs: list[MarketPair] = []

    def register_market_pair(
        self,
        condition_id: str,
        yes_instrument_id: InstrumentId,
        no_instrument_id: InstrumentId,
    ) -> None:
        """
        Queue a YES/NO instrument pair to scan for arb opportunities.

        If called before `on_start()` (e.g., by the BacktestRunner during
        engine setup), the pair is held in `_pending_pairs` and subscribed
        on_start. If called after on_start, subscription happens immediately.
        """
        pair = MarketPair(condition_id, yes_instrument_id, no_instrument_id)
        if not self.is_running:
            self._pending_pairs.append(pair)
            return
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
        # Flush any pre-start pairs queued during engine wiring.
        for pair in self._pending_pairs:
            self._pairs.append(pair)
            self.subscribe_order_book_deltas(pair.yes_instrument_id)
            self.subscribe_order_book_deltas(pair.no_instrument_id)
        self._pending_pairs.clear()
        # If the runner attached a single initial pair tuple, honour it.
        initial = getattr(self, "_initial_pair", None)
        if initial is not None and not self._pairs:
            cid, yes_id, no_id = initial
            pair = MarketPair(cid, yes_id, no_id)
            self._pairs.append(pair)
            self.subscribe_order_book_deltas(yes_id)
            self.subscribe_order_book_deltas(no_id)
        log.info(
            "BinaryArbStrategy started",
            min_profit=self._cfg.min_profit_usdc,
            pairs=len(self._pairs),
        )

    def on_stop(self) -> None:
        log.info("BinaryArbStrategy stopping")
        for pair in self._pairs:
            try:
                self.cancel_all_orders(pair.yes_instrument_id)
                self.cancel_all_orders(pair.no_instrument_id)
            except Exception:
                pass

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """
        Read best ask directly from the incoming delta stream.

        We don't rely on `cache.order_book(iid)` because that requires the
        engine to materialise a maintained book, which doesn't happen with
        only delta subscriptions in backtest. Since our reconstructed book
        snapshots are 1-level snapshots (CLEAR + 2 ADDs), the cheapest
        approach is to scan the deltas list ourselves.
        """
        iid = str(deltas.instrument_id)
        best_ask = None
        best_bid = None
        for d in deltas.deltas:
            if d.action == BookAction.ADD:
                if d.order.side == OrderSide.SELL:
                    p = float(d.order.price)
                    if best_ask is None or p < best_ask:
                        best_ask = p
                elif d.order.side == OrderSide.BUY:
                    p = float(d.order.price)
                    if best_bid is None or p > best_bid:
                        best_bid = p
        if best_ask is None:
            return
        self._best_asks[iid] = best_ask
        self._delta_count = getattr(self, "_delta_count", 0) + 1
        self._scan_for_arb(deltas.instrument_id)

    def _scan_for_arb(self, updated_id: InstrumentId) -> None:
        """Check all pairs that include the updated instrument for arb."""
        for pair in self._pairs:
            if updated_id not in (pair.yes_instrument_id, pair.no_instrument_id):
                continue

            if (
                not getattr(self._cfg, "allow_concurrent", False)
                and pair.condition_id in self._active_arbs
            ):
                continue

            yes_ask = self._best_asks.get(str(pair.yes_instrument_id))
            no_ask = self._best_asks.get(str(pair.no_instrument_id))
            if yes_ask is None or no_ask is None:
                continue

            combined_cost = yes_ask + no_ask
            fee = getattr(self._cfg, "taker_fee", TAKER_FEE)
            profit = 1.0 - combined_cost - fee

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
        # Per-leg notional cap (USDC). Default 5 → typical 5-15 shares per leg.
        leg_notional = float(getattr(self._cfg, "order_notional_usdc", 5.0))
        # Each leg's shares: leg_notional / leg_ask. The two legs purchase the
        # SAME share count (so combined payoff at resolution is `shares * $1`).
        target_shares_yes = leg_notional / max(yes_ask, 0.01)
        target_shares_no = leg_notional / max(no_ask, 0.01)
        size = min(target_shares_yes, target_shares_no)
        # Cap by max_capital across both legs.
        share_cap = self._cfg.max_capital_usdc / (yes_ask + no_ask)
        size = min(size, share_cap)
        size = round(size, 2)
        if size < 5.0:  # Polymarket min_order_size
            size = 5.0

        yes_order = self.order_factory.limit(
            instrument_id=pair.yes_instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str(f"{size:.2f}"),
            price=Price.from_str(f"{yes_ask:.2f}"),
            time_in_force=TimeInForce.IOC,   # immediate-or-cancel for arb
        )
        no_order = self.order_factory.limit(
            instrument_id=pair.no_instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str(f"{size:.2f}"),
            price=Price.from_str(f"{no_ask:.2f}"),
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
        from nautilus_trader.model.identifiers import ClientOrderId

        for cid_str, filled in (
            (arb.yes_order_id, arb.yes_filled),
            (arb.no_order_id, arb.no_filled),
        ):
            if not cid_str or filled:
                continue
            try:
                order = self.cache.order(ClientOrderId(cid_str))
            except Exception:
                order = None
            if order is not None and order.is_open:
                try:
                    self.cancel_order(order)
                except Exception:
                    pass

    def on_reset(self) -> None:
        self._active_arbs.clear()
        self._best_asks.clear()
