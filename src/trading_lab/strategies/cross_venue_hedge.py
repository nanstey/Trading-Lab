from __future__ import annotations

from decimal import Decimal

import structlog
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import BookAction, OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from trading_lab.research.cross_venue_fair_value import AnchoredFairValueModel
from trading_lab.strategies.cross_venue_state import CrossVenueLeggingStateMachine

log = structlog.get_logger(__name__)


class CrossVenueHedgeConfig(StrategyConfig, frozen=True):
    strategy_id: str = "CROSS-VENUE-HEDGE-001"
    observe_only: bool = False
    poly_condition_id: str = ""
    poly_yes_token_id: str = ""
    poly_no_token_id: str = ""
    hl_symbol: str = ""
    hl_network: str = "mainnet"

    fair_value_anchor_price: float = 0.0
    fair_value_scale: float = 2500.0
    fair_value_bias: float = 0.0
    min_probability: float = 0.01
    max_probability: float = 0.99

    hedge_ratio: float = 1.0
    entry_threshold_bps: int = 100
    order_size_usdc: float = 25.0


class CrossVenueHedgeStrategy(Strategy):
    """Config-driven HL/PM cross-venue scaffold with deterministic fair-value and hedge-failure state."""

    def __init__(self, config: CrossVenueHedgeConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._pending_instruments: list[InstrumentId] = []
        self._instruments: list[InstrumentId] = []
        self._touches: dict[str, tuple[float | None, float | None]] = {}
        self._trade_prices: dict[str, float] = {}
        self._yes_instrument_id = None
        self._no_instrument_id = None
        self._hl_instrument_id = None
        self._legging = CrossVenueLeggingStateMachine()
        self._fair_value_model = AnchoredFairValueModel(
            anchor_price=Decimal(str(config.fair_value_anchor_price)),
            scale=Decimal(str(config.fair_value_scale)),
            bias=Decimal(str(config.fair_value_bias)),
            min_probability=Decimal(str(config.min_probability)),
            max_probability=Decimal(str(config.max_probability)),
        )

    def register_instrument(self, instrument_id: InstrumentId) -> None:
        if not self.is_running:
            self._pending_instruments.append(instrument_id)
            return
        self._activate_instrument(instrument_id)

    def register_cross_venue_legs(self, *, yes_instrument_id, no_instrument_id, hl_instrument_id) -> None:
        self._yes_instrument_id = yes_instrument_id
        self._no_instrument_id = no_instrument_id
        self._hl_instrument_id = hl_instrument_id

    def on_hyperliquid_hedge_rejected(self, reason: str) -> None:
        self._legging.on_hyperliquid_reject(reason=reason)
        log.warning("cross-venue hedge rejected", reason=reason, needs_polymarket_flatten=self._legging.needs_polymarket_flatten)

    def on_start(self) -> None:
        for instrument_id in self._pending_instruments:
            self._activate_instrument(instrument_id)
        self._pending_instruments.clear()
        log.info(
            "CrossVenueHedgeStrategy started",
            hl_symbol=self._cfg.hl_symbol,
            anchor_price=self._cfg.fair_value_anchor_price,
            fair_value_scale=self._cfg.fair_value_scale,
        )

    def on_stop(self) -> None:
        return None

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
        self._trade_prices[str(tick.instrument_id)] = float(tick.price)

    def _activate_instrument(self, instrument_id: InstrumentId) -> None:
        self._instruments.append(instrument_id)
        self.subscribe_order_book_deltas(instrument_id)
        self.subscribe_trade_ticks(instrument_id)

    def _hl_price_to_implied_prob(self, hl_price: Decimal) -> Decimal:
        return self._fair_value_model.probability(hl_price)

    def current_fair_value(self, hl_price: Decimal) -> Decimal:
        return self._hl_price_to_implied_prob(hl_price)


__all__ = ["CrossVenueHedgeConfig", "CrossVenueHedgeStrategy"]
