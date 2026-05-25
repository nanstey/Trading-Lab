"""
Wide-Spread Fade Strategy for Polymarket binary markets.

Hypothesis
----------
When the bid-ask spread on a Polymarket binary outcome temporarily widens
well above its short-term median, the wide-side typically refills within a
few seconds at a tighter price. We post a passive BUY order one tick
inside the wider side to capture the refill (mean-reversion edge).

Logic per OrderBookDeltas event
-------------------------------
1. Update best bid + best ask from the delta payload (CLEAR+ADDs on the
   reconstructed snapshot stream).
2. Track a rolling median of recent spread observations.
3. If current_spread > min_spread_tick AND > 2x rolling median, identify
   the side that retreated the most (the "wide" side) and post a passive
   IOC BUY one tick inside that side, sized at fade_size_usdc.
4. IOC means the order auto-cancels if not immediately matched; no
   manual cancel-after-N-deltas bookkeeping is required.
5. Track inventory in USDC notional; skip new entries when net long
   exceeds max_net_inventory_usdc.

Notes
-----
- One tick on Polymarket binary CLOBs is $0.01 (price precision = 2 decimals).
- Only imports nautilus_trader / nautilus_predict / stdlib so the codegen
  guard allowlist is satisfied.
"""

from __future__ import annotations

from collections import deque
from statistics import median

import structlog
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookAction, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

log = structlog.get_logger(__name__)


# One tick on Polymarket binary outcome CLOBs ($0.01).
TICK_SIZE = 0.01


class WideSpreadFadeConfig(StrategyConfig, frozen=True):
    """Configuration for the wide-spread fade strategy."""

    strategy_id: str = "WIDE-SPREAD-FADE-001"
    # Minimum absolute spread (in price units, i.e. USDC per share)
    # below which the strategy does not act. Parameter space:
    # [0.02, 0.03, 0.05].
    min_spread_tick: float = 0.03
    # Notional size per fade order in USDC. Parameter space: [5, 10].
    fade_size_usdc: float = 5.0
    # Rolling window length over which the spread median is computed.
    spread_window: int = 20
    # Multiple of the rolling median above which the spread is "wide".
    wide_multiple: float = 2.0
    # Hard inventory cap (USDC notional). When net long >= cap, new
    # entries are skipped.
    max_net_inventory_usdc: float = 50.0


class WideSpreadFadeStrategy(Strategy):
    """
    Posts passive BUY orders one tick inside the wider side of the book
    when the spread temporarily blows out above its short-term median.

    Instruments are registered via ``register_instrument()`` and
    subscribed in ``on_start()``.
    """

    def __init__(self, config: WideSpreadFadeConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._instruments: list[InstrumentId] = []
        self._pending_instruments: list[InstrumentId] = []
        # Per-instrument rolling spread history.
        self._spread_history: dict[str, deque[float]] = {}
        # Per-instrument last seen best bid / best ask.
        self._best_bid: dict[str, float] = {}
        self._best_ask: dict[str, float] = {}
        # Previous best bid/ask to detect which side retreated the most.
        self._prev_bid: dict[str, float] = {}
        self._prev_ask: dict[str, float] = {}
        # Net inventory USDC (positive == long). Tracked across all
        # instruments registered to this strategy.
        self._net_inventory_usdc: float = 0.0
        # Outstanding fade order client_order_ids (so we can match fills).
        self._open_orders: set[str] = set()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_instrument(self, instrument_id: InstrumentId) -> None:
        """Queue an instrument to be subscribed on start (or now if running)."""
        if not self.is_running:
            self._pending_instruments.append(instrument_id)
            return
        self._instruments.append(instrument_id)
        self.subscribe_order_book_deltas(instrument_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        for iid in self._pending_instruments:
            self._instruments.append(iid)
            self.subscribe_order_book_deltas(iid)
        self._pending_instruments.clear()
        log.info(
            "WideSpreadFadeStrategy started",
            min_spread_tick=self._cfg.min_spread_tick,
            fade_size_usdc=self._cfg.fade_size_usdc,
            spread_window=self._cfg.spread_window,
            wide_multiple=self._cfg.wide_multiple,
            instruments=len(self._instruments),
        )

    def on_stop(self) -> None:
        log.info("WideSpreadFadeStrategy stopping")
        for iid in self._instruments:
            try:
                self.cancel_all_orders(iid)
            except Exception:
                pass

    def on_reset(self) -> None:
        self._spread_history.clear()
        self._best_bid.clear()
        self._best_ask.clear()
        self._prev_bid.clear()
        self._prev_ask.clear()
        self._open_orders.clear()
        self._net_inventory_usdc = 0.0

    # ------------------------------------------------------------------
    # Book handling
    # ------------------------------------------------------------------
    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """Extract best bid/ask from the delta stream and act on widenings."""
        iid_str = str(deltas.instrument_id)

        best_bid = self._best_bid.get(iid_str)
        best_ask = self._best_ask.get(iid_str)
        new_bid: float | None = None
        new_ask: float | None = None

        for d in deltas.deltas:
            if d.action == BookAction.CLEAR:
                new_bid = None
                new_ask = None
                continue
            if d.action != BookAction.ADD:
                continue
            order = d.order
            price = float(order.price)
            if order.side == OrderSide.SELL:
                if new_ask is None or price < new_ask:
                    new_ask = price
            elif order.side == OrderSide.BUY:
                if new_bid is None or price > new_bid:
                    new_bid = price

        # If this delta payload didn't touch a side, keep the prior value.
        if new_bid is None:
            new_bid = best_bid
        if new_ask is None:
            new_ask = best_ask

        if new_bid is None or new_ask is None:
            return

        # Snapshot previous values BEFORE overwriting.
        prev_bid = best_bid
        prev_ask = best_ask
        self._prev_bid[iid_str] = prev_bid if prev_bid is not None else new_bid
        self._prev_ask[iid_str] = prev_ask if prev_ask is not None else new_ask
        self._best_bid[iid_str] = new_bid
        self._best_ask[iid_str] = new_ask

        spread = new_ask - new_bid
        if spread <= 0:
            return

        history = self._spread_history.setdefault(
            iid_str, deque(maxlen=self._cfg.spread_window),
        )
        history.append(spread)
        # Need a reasonable sample to compute a median.
        if len(history) < max(5, self._cfg.spread_window // 2):
            return

        rolling_median = median(history)
        if rolling_median <= 0:
            return

        if spread <= self._cfg.min_spread_tick:
            return
        if spread <= self._cfg.wide_multiple * rolling_median:
            return

        # Inventory cap.
        if self._net_inventory_usdc >= self._cfg.max_net_inventory_usdc:
            return

        self._fade(deltas.instrument_id, new_bid, new_ask)

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def _fade(
        self,
        instrument_id: InstrumentId,
        best_bid: float,
        best_ask: float,
    ) -> None:
        """Post a passive BUY order one tick inside the wider side."""
        iid_str = str(instrument_id)
        prev_bid = self._prev_bid.get(iid_str, best_bid)
        prev_ask = self._prev_ask.get(iid_str, best_ask)

        bid_retreat = max(0.0, prev_bid - best_bid)
        ask_retreat = max(0.0, best_ask - prev_ask)

        # Pick the side that retreated the most. If neither side moved
        # (spread is wide for a different reason), default to bidding
        # one tick above the current bid — a passive maker improvement.
        if ask_retreat >= bid_retreat:
            # Ask side widened; post one tick BELOW the new best ask.
            target_price = best_ask - TICK_SIZE
        else:
            # Bid side dropped; post one tick ABOVE the new best bid.
            target_price = best_bid + TICK_SIZE

        # Keep target strictly between the current bid and ask.
        upper = best_ask - TICK_SIZE
        lower = best_bid + TICK_SIZE
        if upper < lower:
            return
        if target_price > upper:
            target_price = upper
        if target_price < lower:
            target_price = lower

        # Polymarket prices are quoted to 2dp; round defensively.
        target_price = round(target_price, 2)
        if target_price <= 0.0 or target_price >= 1.0:
            return

        size = self._cfg.fade_size_usdc / max(target_price, TICK_SIZE)
        size = round(size, 2)
        # Polymarket per-order minimum is 5 shares.
        if size < 5.0:
            size = 5.0

        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str(f"{size:.2f}"),
            price=Price.from_str(f"{target_price:.2f}"),
            time_in_force=TimeInForce.IOC,
        )
        coid = str(order.client_order_id)
        self._open_orders.add(coid)
        self.submit_order(order)

        log.info(
            "Wide-spread fade order submitted",
            instrument=iid_str,
            best_bid=best_bid,
            best_ask=best_ask,
            target_price=target_price,
            size=size,
            inventory_usdc=self._net_inventory_usdc,
        )

    # ------------------------------------------------------------------
    # Fills / cancels
    # ------------------------------------------------------------------
    def on_order_filled(self, event) -> None:
        coid = str(event.client_order_id)
        if coid not in self._open_orders:
            return
        try:
            filled_qty = float(event.last_qty)
            filled_px = float(event.last_px)
        except Exception:
            return
        # All fade orders are BUYs → inventory grows long.
        self._net_inventory_usdc += filled_qty * filled_px
        log.info(
            "Fade order fill",
            client_order_id=coid,
            qty=filled_qty,
            price=filled_px,
            net_inventory_usdc=self._net_inventory_usdc,
        )

    def on_order_canceled(self, event) -> None:
        coid = str(event.client_order_id)
        self._open_orders.discard(coid)
