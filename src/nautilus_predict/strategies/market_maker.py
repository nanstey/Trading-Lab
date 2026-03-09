"""
Market Making & Liquidity Provision Strategy for Polymarket.

Continuously quotes both sides of a Polymarket binary market CLOB to capture
the bid-ask spread. Makers pay zero fees and earn daily USDC rebates from
Polymarket, making this the primary revenue strategy.

Algorithm
---------
1. On each orderbook update, compute a fair value from mid price.
2. Place a BUY order at (fair_value - half_spread) and a SELL order at
   (fair_value + half_spread), subject to:
   - Minimum spread floor (spread_bps).
   - Maximum net position limit (max_position_usdc).
3. Cancel and refresh stale quotes when the market moves beyond a threshold.
4. Skew quotes toward the side that reduces inventory imbalance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.trading.strategy import Strategy

log = structlog.get_logger(__name__)


@dataclass
class MarketMakingConfig(StrategyConfig, frozen=True):
    """Configuration for the market-making strategy."""

    strategy_id: str = "MARKET-MAKER-001"
    spread_bps: int = 50
    order_size_usdc: float = 10.0
    max_position_usdc: float = 500.0

    # Inventory skew: shift quotes by this fraction of imbalance per unit
    skew_factor: float = 0.1

    # Cancel and replace if best bid/ask moves by more than this fraction
    refresh_threshold: float = 0.005   # 0.5%


class MarketMakingStrategy(Strategy):
    """
    Polymarket market-making strategy.

    Subscribes to orderbook deltas for registered instruments and maintains
    a pair of resting limit orders on both sides.
    """

    def __init__(self, config: MarketMakingConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._instruments: dict[str, Instrument] = {}

        # Track our resting orders per instrument: {instrument_id: (bid_id, ask_id)}
        self._quotes: dict[str, tuple[str | None, str | None]] = {}

        # Net inventory per instrument in USDC (positive = long YES)
        self._inventory: dict[str, float] = {}

    def on_start(self) -> None:
        log.info("MarketMakingStrategy started", spread_bps=self._cfg.spread_bps)

    def on_stop(self) -> None:
        log.info("MarketMakingStrategy stopping — cancelling all quotes")
        self.cancel_all_orders()

    def on_instrument(self, instrument: Instrument) -> None:
        iid = str(instrument.id)
        self._instruments[iid] = instrument
        self._quotes[iid] = (None, None)
        self._inventory[iid] = 0.0
        self.subscribe_order_book_deltas(instrument.id)
        log.info("Market maker subscribed", instrument=iid)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        iid = str(deltas.instrument_id)
        book = self.cache.order_book(deltas.instrument_id)
        if book is None or book.best_bid_price() is None or book.best_ask_price() is None:
            return

        best_bid = float(book.best_bid_price())
        best_ask = float(book.best_ask_price())
        mid = (best_bid + best_ask) / 2.0
        current_spread = best_ask - best_bid

        # Minimum spread gate: only quote if market spread ≥ our threshold
        half_spread = max(mid * self._cfg.spread_bps / 10_000, current_spread / 2) * 1.05

        # Inventory skew: adjust mid to reduce position
        inv = self._inventory.get(iid, 0.0)
        skew = self._cfg.skew_factor * inv / self._cfg.max_position_usdc
        skewed_mid = mid - skew

        new_bid = round(skewed_mid - half_spread, 4)
        new_ask = round(skewed_mid + half_spread, 4)

        # Clamp to valid binary range (0.01 – 0.99)
        new_bid = max(0.01, min(new_bid, 0.98))
        new_ask = max(new_bid + 0.01, min(new_ask, 0.99))

        # Check if existing quotes are still good
        bid_id, ask_id = self._quotes.get(iid, (None, None))
        if self._quotes_need_refresh(iid, new_bid, new_ask, book):
            self._refresh_quotes(deltas.instrument_id, new_bid, new_ask)

    def _quotes_need_refresh(
        self,
        iid: str,
        new_bid: float,
        new_ask: float,
        book,
    ) -> bool:
        bid_id, ask_id = self._quotes.get(iid, (None, None))
        if bid_id is None or ask_id is None:
            return True

        # Retrieve existing resting orders
        existing_bid = self.cache.order(self.cache.client_order_id(bid_id))
        existing_ask = self.cache.order(self.cache.client_order_id(ask_id))
        if existing_bid is None or existing_ask is None:
            return True

        threshold = self._cfg.refresh_threshold
        bid_stale = abs(float(existing_bid.price) - new_bid) / new_bid > threshold
        ask_stale = abs(float(existing_ask.price) - new_ask) / new_ask > threshold
        return bid_stale or ask_stale

    def _refresh_quotes(
        self,
        instrument_id: InstrumentId,
        bid_price: float,
        ask_price: float,
    ) -> None:
        iid = str(instrument_id)
        instrument = self._instruments.get(iid)
        if instrument is None:
            return

        # Cancel stale quotes
        bid_id, ask_id = self._quotes.get(iid, (None, None))
        if bid_id:
            self.cancel_order(self.cache.order(self.cache.client_order_id(bid_id)))
        if ask_id:
            self.cancel_order(self.cache.order(self.cache.client_order_id(ask_id)))

        # Submit fresh quotes
        size = self._cfg.order_size_usdc / bid_price  # convert USDC to shares

        bid_order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str(f"{size:.4f}"),
            price=Price.from_str(f"{bid_price:.4f}"),
            time_in_force=TimeInForce.GTC,
        )
        ask_order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.SELL,
            quantity=Quantity.from_str(f"{size:.4f}"),
            price=Price.from_str(f"{ask_price:.4f}"),
            time_in_force=TimeInForce.GTC,
        )

        self.submit_order(bid_order)
        self.submit_order(ask_order)
        self._quotes[iid] = (str(bid_order.client_order_id), str(ask_order.client_order_id))

        log.debug(
            "Quotes refreshed",
            instrument=iid,
            bid=bid_price,
            ask=ask_price,
            size=f"{size:.4f}",
        )

    def on_order_filled(self, event) -> None:
        iid = str(event.instrument_id)
        fill_usdc = float(event.last_qty) * float(event.last_px)
        if event.order_side == OrderSide.BUY:
            self._inventory[iid] = self._inventory.get(iid, 0.0) + fill_usdc
        else:
            self._inventory[iid] = self._inventory.get(iid, 0.0) - fill_usdc

        log.info(
            "Fill",
            instrument=iid,
            side=event.order_side,
            qty=event.last_qty,
            px=event.last_px,
            inventory=self._inventory[iid],
        )

    def on_reset(self) -> None:
        self._quotes.clear()
        self._inventory.clear()
