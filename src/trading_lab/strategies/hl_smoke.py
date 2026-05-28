"""
Hyperliquid smoke strategy.

Plumbing-only strategy that exercises the full HL paper / testnet path
end-to-end. Every `quote_interval_secs` seconds, the strategy reads the
current best-bid / best-ask from the order book and submits one limit
BUY a configurable offset below mid and one limit SELL the same offset
above mid, both IOC. No edge claim — the goal is to surface integration
issues (auth, signing, rate-limits, reconnect, fill plumbing) before any
real strategy is written for HL.

Never promote past `LIVE_READY` (the testnet shakedown state). Stays
documented in `research/hypotheses/hl-smoke.md`.
"""

from __future__ import annotations

from typing import Any

import structlog
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import BookAction, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

log = structlog.get_logger(__name__)


class HLSmokeConfig(StrategyConfig, frozen=True):
    """Configuration for the HL smoke strategy."""

    strategy_id: str = "HL-SMOKE-001"
    # Distance from mid to place each side, in basis points.
    offset_bps: int = 100  # 1.0%
    # How often to refresh the quote pair.
    quote_interval_secs: int = 60
    # Per-side order size in BASE asset units (e.g. BTC). HL perp size is
    # base-denominated. Keep tiny — this is plumbing, not a strategy.
    order_size_base: float = 0.001
    # Price precision (decimal places). HL ticks vary by asset; 2 is a
    # safe default for major perps that quote in USDC.
    price_precision: int = 2


class HLSmokeStrategy(Strategy):
    """
    No-op quoting strategy for Hyperliquid integration smoke tests.

    Every `quote_interval_secs`, places one BUY at (mid - offset) and one
    SELL at (mid + offset). IOC orders — they either fill on the touch or
    cancel out, so we don't pile up open orders. Designed to be cheap and
    legible in logs.
    """

    def __init__(self, config: HLSmokeConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._instruments: list[InstrumentId] = []
        self._pending_instruments: list[InstrumentId] = []
        # iid_str → (best_bid, best_ask)
        self._touches: dict[str, tuple[float | None, float | None]] = {}
        self._timer_name = "hl-smoke-quote-timer"

    # ------------------------------------------------------------------
    # Registration (called by the runner before start)
    # ------------------------------------------------------------------
    def register_instrument(self, instrument_id: InstrumentId) -> None:
        if not self.is_running:
            self._pending_instruments.append(instrument_id)
            return
        self._instruments.append(instrument_id)
        self.subscribe_order_book_deltas(instrument_id)
        self.subscribe_trade_ticks(instrument_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        for iid in self._pending_instruments:
            self._instruments.append(iid)
            try:
                self.subscribe_order_book_deltas(iid)
            except Exception as exc:
                log.warning("subscribe deltas failed", iid=str(iid), error=str(exc))
            try:
                self.subscribe_trade_ticks(iid)
            except Exception as exc:
                log.warning("subscribe ticks failed", iid=str(iid), error=str(exc))
        self._pending_instruments.clear()

        log.info(
            "HLSmokeStrategy started",
            instruments=len(self._instruments),
            offset_bps=self._cfg.offset_bps,
            quote_interval_secs=self._cfg.quote_interval_secs,
        )

        try:
            self.clock.set_timer(
                name=self._timer_name,
                interval=_secs_to_timedelta(self._cfg.quote_interval_secs),
                callback=self._on_timer,
            )
        except Exception as exc:
            log.warning("could not set quote timer; will rely on first tick", error=str(exc))

    def on_stop(self) -> None:
        log.info("HLSmokeStrategy stopping")
        try:
            self.clock.cancel_timer(self._timer_name)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------
    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        iid_str = str(deltas.instrument_id)
        best_bid, best_ask = self._touches.get(iid_str, (None, None))
        for d in deltas.deltas:
            if d.action == BookAction.CLEAR:
                best_bid, best_ask = None, None
                continue
            if d.action not in (BookAction.ADD, BookAction.UPDATE):
                continue
            try:
                px = float(d.order.price)
                sz = float(d.order.size)
            except Exception:
                continue
            if sz <= 0:
                continue
            if d.order.side == OrderSide.BUY:
                if best_bid is None or px > best_bid:
                    best_bid = px
            elif d.order.side == OrderSide.SELL:
                if best_ask is None or px < best_ask:
                    best_ask = px
        self._touches[iid_str] = (best_bid, best_ask)

    def on_trade_tick(self, tick: TradeTick) -> None:
        iid_str = str(tick.instrument_id)
        px = float(tick.price)
        best_bid, best_ask = self._touches.get(iid_str, (None, None))
        if best_bid is None:
            best_bid = px
        if best_ask is None:
            best_ask = px
        self._touches[iid_str] = (best_bid, best_ask)

    # ------------------------------------------------------------------
    # Timer callback — quote pair on every instrument with a known mid
    # ------------------------------------------------------------------
    def _on_timer(self, event: Any) -> None:
        for iid in self._instruments:
            best_bid, best_ask = self._touches.get(str(iid), (None, None))
            if best_bid is None or best_ask is None:
                continue
            mid = (best_bid + best_ask) / 2.0
            offset = mid * (self._cfg.offset_bps / 10_000.0)
            buy_px = round(mid - offset, self._cfg.price_precision)
            sell_px = round(mid + offset, self._cfg.price_precision)
            if buy_px <= 0 or sell_px <= 0:
                continue
            self._submit_pair(iid, buy_px, sell_px)

    def _submit_pair(self, instrument_id: InstrumentId, buy_px: float, sell_px: float) -> None:
        qty_str = _format_quantity(self._cfg.order_size_base)
        for side, px in ((OrderSide.BUY, buy_px), (OrderSide.SELL, sell_px)):
            order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=side,
                quantity=Quantity.from_str(qty_str),
                price=Price.from_str(f"{px:.{self._cfg.price_precision}f}"),
                time_in_force=TimeInForce.IOC,
            )
            try:
                self.submit_order(order)
            except Exception as exc:
                log.warning("hl-smoke submit failed",
                            side=str(side), price=px, error=str(exc))
                continue
            self.log.info(
                f"hl-smoke {side}: px={px} qty={qty_str} iid={instrument_id}",
                color=LogColor.CYAN,
            )


def _secs_to_timedelta(secs: int):
    from datetime import timedelta
    return timedelta(seconds=max(1, int(secs)))


def _format_quantity(size: float) -> str:
    # Sized for tiny smoke trades on majors; 6dp covers down to 1e-6.
    return f"{size:.6f}"
