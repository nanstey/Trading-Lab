from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class CrossVenueObserveConfig(StrategyConfig, frozen=True):
    strategy_id: str = "CROSS-VENUE-OBSERVE-001"
    observe_only: bool = True
    poly_condition_id: str = ""
    poly_yes_token_id: str = ""
    poly_no_token_id: str = ""
    hl_symbol: str = ""
    hl_network: str = "mainnet"


class CrossVenueObserveStrategy(Strategy):
    """Observe-only dual-venue scaffold for HL/PM cross-venue work."""

    def __init__(self, config: CrossVenueObserveConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._pending_instruments: list[InstrumentId] = []
        self._instruments: list[InstrumentId] = []
        self._book_updates = 0
        self._trade_ticks = 0

    def register_instrument(self, instrument_id: InstrumentId) -> None:
        if not self.is_running:
            self._pending_instruments.append(instrument_id)
            return
        self._activate_instrument(instrument_id)

    def on_start(self) -> None:
        for instrument_id in self._pending_instruments:
            self._activate_instrument(instrument_id)
        self._pending_instruments.clear()

    def on_stop(self) -> None:
        return None

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        self._book_updates += 1

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._trade_ticks += 1

    def _activate_instrument(self, instrument_id: InstrumentId) -> None:
        self._instruments.append(instrument_id)
        self.subscribe_order_book_deltas(instrument_id)
        self.subscribe_trade_ticks(instrument_id)
