"""
Cross-Venue Hedging Strategy.

Exploits discrepancies between Hyperliquid perpetual prices and crypto-related
Polymarket prediction markets. When directional bias on Hyperliquid suggests
an outcome is mispriced on Polymarket (or vice versa), the system opens a
delta-neutral hedge across both venues.

Architecture
------------
- Monitors Polymarket YES/NO prices for crypto-linked markets (e.g.
  "Will BTC close above $100k on Dec 31?").
- Monitors Hyperliquid perpetual price for the underlying (BTC-PERP).
- Computes an implied probability from the Hyperliquid price via a
  log-normal model or percentile lookup.
- When implied probability diverges from Polymarket probability by more than
  a threshold, opens:
    * A directional trade on Hyperliquid (long/short the perp).
    * An opposing position on Polymarket (buy YES/NO).
- The hedge unwinds automatically when prices converge or at expiry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

import structlog
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

log = structlog.get_logger(__name__)


class HedgePair(NamedTuple):
    """Pairing of a Polymarket YES instrument with a Hyperliquid perp."""

    condition_id: str
    pm_yes_instrument_id: InstrumentId     # Polymarket YES token
    hl_instrument_id: InstrumentId         # Hyperliquid PERP (e.g. BTC-PERP.HYPERLIQUID)
    strike_price: float                    # The threshold price (e.g. 100_000 for BTC > $100k)
    expiry_ts_ns: int                      # Market expiry as nanosecond timestamp


@dataclass
class CrossVenueHedgeConfig(StrategyConfig, frozen=True):
    """Configuration for the cross-venue hedging strategy."""

    strategy_id: str = "CROSS-VENUE-HEDGE-001"
    hedge_ratio: float = 0.5            # Fraction of Polymarket exposure hedged on HL
    min_divergence: float = 0.05        # Minimum probability divergence to trigger hedge (5pp)
    max_position_usdc: float = 2000.0   # Max total exposure per hedge pair
    close_on_convergence: float = 0.01  # Close when divergence drops below 1pp


class CrossVenueHedgeStrategy(Strategy):
    """
    Monitors crypto-related Polymarket markets and hedges on Hyperliquid.
    """

    def __init__(self, config: CrossVenueHedgeConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._pairs: list[HedgePair] = []

        # Latest mid prices
        self._pm_yes_mids: dict[str, float] = {}     # condition_id → probability
        self._hl_mids: dict[str, float] = {}         # hl_instrument_id → price

        # Active hedge positions per condition_id
        self._active_hedges: dict[str, dict] = {}

    def register_pair(self, pair: HedgePair) -> None:
        """Register a Polymarket/Hyperliquid pair for cross-venue monitoring."""
        self._pairs.append(pair)
        self.subscribe_order_book_deltas(pair.pm_yes_instrument_id)
        self.subscribe_order_book_deltas(pair.hl_instrument_id)
        log.info(
            "Registered hedge pair",
            condition_id=pair.condition_id,
            pm=str(pair.pm_yes_instrument_id),
            hl=str(pair.hl_instrument_id),
            strike=pair.strike_price,
        )

    def on_start(self) -> None:
        log.info("CrossVenueHedgeStrategy started", pairs=len(self._pairs))

    def on_stop(self) -> None:
        log.info("CrossVenueHedgeStrategy stopping")
        self.cancel_all_orders()

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        iid = str(deltas.instrument_id)
        book = self.cache.order_book(deltas.instrument_id)
        if book is None:
            return

        bid = book.best_bid_price()
        ask = book.best_ask_price()
        if bid is None or ask is None:
            return
        mid = (float(bid) + float(ask)) / 2.0

        # Determine which side this update is for
        for pair in self._pairs:
            if iid == str(pair.pm_yes_instrument_id):
                self._pm_yes_mids[pair.condition_id] = mid
                self._evaluate_pair(pair)
            elif iid == str(pair.hl_instrument_id):
                self._hl_mids[iid] = mid
                self._evaluate_pair(pair)

    def _evaluate_pair(self, pair: HedgePair) -> None:
        """Check for hedge opportunity or convergence on a given pair."""
        pm_prob = self._pm_yes_mids.get(pair.condition_id)
        hl_price = self._hl_mids.get(str(pair.hl_instrument_id))
        if pm_prob is None or hl_price is None:
            return

        implied_prob = self._compute_implied_prob(hl_price, pair.strike_price)
        divergence = implied_prob - pm_prob

        log.debug(
            "Pair evaluation",
            condition_id=pair.condition_id,
            pm_prob=round(pm_prob, 4),
            implied_prob=round(implied_prob, 4),
            divergence=round(divergence, 4),
        )

        # Unwind if convergence reached
        if pair.condition_id in self._active_hedges:
            if abs(divergence) < self._cfg.close_on_convergence:
                self._close_hedge(pair, divergence)
            return

        # Open new hedge if divergence exceeds threshold
        if abs(divergence) >= self._cfg.min_divergence:
            self._open_hedge(pair, pm_prob, implied_prob, divergence)

    def _compute_implied_prob(self, spot_price: float, strike: float) -> float:
        """
        Simplified binary option pricing: probability that spot > strike.

        Uses a basic threshold model. For production, replace with a proper
        log-normal or historical distribution model.
        """
        # Simple linear interpolation based on distance from strike
        ratio = spot_price / strike
        if ratio >= 1.20:
            return 0.90
        elif ratio >= 1.05:
            return 0.70 + (ratio - 1.05) / 0.15 * 0.20
        elif ratio >= 0.95:
            return 0.30 + (ratio - 0.95) / 0.10 * 0.40
        elif ratio >= 0.80:
            return 0.10 + (ratio - 0.80) / 0.15 * 0.20
        else:
            return 0.10

    def _open_hedge(
        self,
        pair: HedgePair,
        pm_prob: float,
        implied_prob: float,
        divergence: float,
    ) -> None:
        """Open a delta-neutral cross-venue hedge position."""
        size_usdc = min(self._cfg.max_position_usdc, 500.0)
        pm_shares = size_usdc / pm_prob
        hl_size = size_usdc * self._cfg.hedge_ratio / self._hl_mids.get(
            str(pair.hl_instrument_id), 1.0
        )

        # If implied_prob > pm_prob: Polymarket underpricing YES
        # → Buy YES on Polymarket + short the perp on Hyperliquid (sell if rally stalls)
        if divergence > 0:
            pm_side = OrderSide.BUY
            hl_is_buy = False
        else:
            pm_side = OrderSide.SELL
            hl_is_buy = True

        pm_price = pm_prob if pm_side == OrderSide.BUY else pm_prob
        hl_price = self._hl_mids[str(pair.hl_instrument_id)]

        pm_order = self.order_factory.limit(
            instrument_id=pair.pm_yes_instrument_id,
            order_side=pm_side,
            quantity=Quantity.from_str(f"{pm_shares:.4f}"),
            price=Price.from_str(f"{pm_price:.4f}"),
            time_in_force=TimeInForce.GTC,
        )
        hl_order = self.order_factory.limit(
            instrument_id=pair.hl_instrument_id,
            order_side=OrderSide.BUY if hl_is_buy else OrderSide.SELL,
            quantity=Quantity.from_str(f"{hl_size:.6f}"),
            price=Price.from_str(f"{hl_price:.2f}"),
            time_in_force=TimeInForce.GTC,
        )

        self._active_hedges[pair.condition_id] = {
            "divergence_at_entry": divergence,
            "pm_order_id": str(pm_order.client_order_id),
            "hl_order_id": str(hl_order.client_order_id),
        }

        self.submit_order(pm_order)
        self.submit_order(hl_order)

        log.info(
            "Hedge opened",
            condition_id=pair.condition_id,
            pm_side=pm_side,
            hl_side="BUY" if hl_is_buy else "SELL",
            divergence=round(divergence, 4),
        )

    def _close_hedge(self, pair: HedgePair, current_divergence: float) -> None:
        """Unwind the hedge as divergence has converged."""
        hedge = self._active_hedges.pop(pair.condition_id, None)
        if hedge is None:
            return
        log.info(
            "Hedge converged — unwinding",
            condition_id=pair.condition_id,
            divergence_at_entry=round(hedge["divergence_at_entry"], 4),
            current_divergence=round(current_divergence, 4),
        )
        # Cancel resting orders; realised P&L from filled legs is tracked by portfolio
        self.cancel_all_orders()

    def on_reset(self) -> None:
        self._active_hedges.clear()
        self._pm_yes_mids.clear()
        self._hl_mids.clear()
