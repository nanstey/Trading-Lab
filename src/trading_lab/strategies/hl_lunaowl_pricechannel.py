"""Hyperliquid LunaOwl PriceChannel clone for AlphaInsider / TradingView research."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from trading_lab.strategies.hl_signal_ops import rolling_high, rolling_low


class HLLunaOwlPriceChannelConfig(StrategyConfig, frozen=True):
    strategy_id: str = "HL-LUNAOWL-PRICECHANNEL-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None

    channel_length: int = 21
    notional_usdc: float = 1_000.0
    allow_short: bool = True
    exit_on_midline_reentry: bool = False


@dataclass(frozen=True)
class PriceChannelSnapshot:
    upper: float
    lower: float
    midpoint: float
    gap_state: str


def compute_price_channel(
    *,
    highs: list[float],
    lows: list[float],
    channel_length: int,
) -> PriceChannelSnapshot | None:
    """Return the completed-bar price channel excluding the current bar."""
    if channel_length <= 0:
        raise ValueError("channel_length must be positive")
    if len(highs) < channel_length + 1 or len(lows) < channel_length + 1:
        return None

    upper = rolling_high(highs[:-1], length=channel_length)
    lower = rolling_low(lows[:-1], length=channel_length)
    midpoint = (upper + lower) / 2.0
    gap_state = "flat"
    if upper > lower:
        gap_state = "gapUp"
    elif upper < lower:
        gap_state = "gapDown"
    return PriceChannelSnapshot(
        upper=upper,
        lower=lower,
        midpoint=midpoint,
        gap_state=gap_state,
    )


def decide_pricechannel_action(
    *,
    close: float,
    snapshot: PriceChannelSnapshot,
    position_side: str,
    allow_short: bool,
    exit_on_midline_reentry: bool,
) -> str:
    """
    Conservative first-pass breakout policy.

    The recovered Pine places stop orders at the channel rails. For a bounded
    HL-native first pass we require bar-close confirmation instead of assuming
    intrabar touch fills. That preserves the breakout family without optimistic
    stop-execution assumptions.
    """
    broke_above = close > snapshot.upper
    broke_below = close < snapshot.lower
    inside_channel = snapshot.lower <= close <= snapshot.upper
    crossed_midpoint = close < snapshot.midpoint if position_side == "LONG" else close > snapshot.midpoint

    if position_side == "FLAT":
        if broke_above:
            return "ENTER_LONG"
        if allow_short and broke_below:
            return "ENTER_SHORT"
        return "HOLD"

    if position_side == "LONG":
        if allow_short and broke_below:
            return "FLIP_SHORT"
        if exit_on_midline_reentry and inside_channel and crossed_midpoint:
            return "EXIT"
        return "HOLD"

    if position_side == "SHORT":
        if broke_above:
            return "FLIP_LONG"
        if exit_on_midline_reentry and inside_channel and crossed_midpoint:
            return "EXIT"
        return "HOLD"

    raise ValueError(f"unknown position_side={position_side}")


class HLLunaOwlPriceChannelStrategy(Strategy):
    """Bar-close price-channel breakout strategy for a single HL perp instrument."""

    def __init__(self, config: HLLunaOwlPriceChannelConfig) -> None:
        super().__init__(config)
        self._cfg = config
        window = config.channel_length + 1
        self._highs: deque[float] = deque(maxlen=window)
        self._lows: deque[float] = deque(maxlen=window)
        self._closes: deque[float] = deque(maxlen=window)
        self._position_side: str = "FLAT"

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("HLLunaOwlPriceChannelStrategy requires bar_type + instrument_id")
        self.subscribe_bars(self._cfg.bar_type)

    def on_stop(self) -> None:
        try:
            self.close_all_positions(self._cfg.instrument_id)
        except Exception:
            pass

    def on_bar(self, bar: Bar) -> None:
        close = float(bar.close)
        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))
        self._closes.append(close)

        snapshot = compute_price_channel(
            highs=list(self._highs),
            lows=list(self._lows),
            channel_length=self._cfg.channel_length,
        )
        if snapshot is None:
            return

        action = decide_pricechannel_action(
            close=close,
            snapshot=snapshot,
            position_side=self._position_side,
            allow_short=self._cfg.allow_short,
            exit_on_midline_reentry=self._cfg.exit_on_midline_reentry,
        )

        instrument = self.cache.instrument(self._cfg.instrument_id)
        if instrument is None:
            return

        qty_units = max(self._cfg.notional_usdc / max(close, 1e-9), 0.0)
        size = Quantity(qty_units, instrument.size_precision)
        if float(size) <= 0:
            return

        if action == "ENTER_LONG":
            self._send(OrderSide.BUY, size)
            self._position_side = "LONG"
        elif action == "ENTER_SHORT":
            self._send(OrderSide.SELL, size)
            self._position_side = "SHORT"
        elif action == "EXIT":
            exit_side = OrderSide.SELL if self._position_side == "LONG" else OrderSide.BUY
            self._send(exit_side, size)
            self._position_side = "FLAT"
        elif action == "FLIP_LONG":
            self._send(OrderSide.BUY, Quantity(2 * float(size), instrument.size_precision))
            self._position_side = "LONG"
        elif action == "FLIP_SHORT":
            self._send(OrderSide.SELL, Quantity(2 * float(size), instrument.size_precision))
            self._position_side = "SHORT"

    def _send(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)


__all__ = [
    "compute_price_channel",
    "decide_pricechannel_action",
    "HLLunaOwlPriceChannelConfig",
    "HLLunaOwlPriceChannelStrategy",
    "PriceChannelSnapshot",
]
