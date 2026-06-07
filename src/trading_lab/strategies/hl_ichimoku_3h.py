"""Hyperliquid Ichimoku 3h clone for AlphaInsider / TradingView research.

This is a conservative first-pass implementation from recovered public evidence.
The source preserved the core Ichimoku lengths, RSI threshold, volatility gate
threshold, and the existence of explicit long/short entry + close logic, but it
did not give fully unambiguous parity on every intermediate helper. We therefore
keep the signal policy explicit and modest:
- native 3h logic runs on strict 1h->3h resampled bars upstream
- standard Ichimoku midpoint lines for Tenkan/Kijun/Senkou B
- standard RSI(14) as the direction filter
- ATR-percent moving average as the volatility gate proxy
- bar-close cloud / Kijun / Chikou-style confirmation for entries and exits
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from trading_lab.strategies.hl_signal_ops import true_range


class HLIchimoku3HConfig(StrategyConfig, frozen=True):
    strategy_id: str = "HL-ICHIMOKU-3H-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None

    tenkan_length: int = 22
    kijun_length: int = 60
    senkou_b_length: int = 120
    displacement: int = 30
    rsi_length: int = 14
    rsi_threshold: float = 50.0
    volatility_length: int = 14
    volatility_gate_threshold: float = 0.2
    notional_usdc: float = 1_000.0
    allow_short: bool = True


@dataclass(frozen=True)
class IchimokuSnapshot:
    close: float
    tenkan: float
    kijun: float
    span_a: float
    span_b: float
    chikou_reference_close: float
    rsi: float
    volatility_pct: float
    rsi_threshold: float
    volatility_gate_threshold: float

    @property
    def cloud_upper(self) -> float:
        return max(self.span_a, self.span_b)

    @property
    def cloud_lower(self) -> float:
        return min(self.span_a, self.span_b)

    @property
    def above_cloud(self) -> bool:
        return self.close > self.cloud_upper

    @property
    def below_cloud(self) -> bool:
        return self.close < self.cloud_lower

    @property
    def chikou_bullish(self) -> bool:
        return self.close > self.chikou_reference_close

    @property
    def chikou_bearish(self) -> bool:
        return self.close < self.chikou_reference_close

    @property
    def volatility_ok(self) -> bool:
        return self.volatility_pct > self.volatility_gate_threshold

    @property
    def long_entry_ready(self) -> bool:
        return (
            self.above_cloud
            and self.tenkan > self.kijun
            and self.chikou_bullish
            and self.rsi > self.rsi_threshold
            and self.volatility_ok
        )

    @property
    def short_entry_ready(self) -> bool:
        return (
            self.below_cloud
            and self.tenkan < self.kijun
            and self.chikou_bearish
            and self.rsi < self.rsi_threshold
            and self.volatility_ok
        )

    @property
    def long_exit_ready(self) -> bool:
        return self.close < self.kijun or not self.above_cloud or self.rsi <= self.rsi_threshold

    @property
    def short_exit_ready(self) -> bool:
        return self.close > self.kijun or not self.below_cloud or self.rsi >= self.rsi_threshold


def _midpoint(highs: list[float], lows: list[float], *, length: int) -> float:
    window_high = max(highs[-length:])
    window_low = min(lows[-length:])
    return (window_high + window_low) / 2.0


def _rsi(closes: list[float], *, length: int) -> float:
    deltas = [curr - prev for prev, curr in zip(closes[-(length + 1) :], closes[-length:], strict=False)]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _volatility_pct(highs: list[float], lows: list[float], closes: list[float], *, length: int) -> float:
    true_ranges = []
    for idx in range(len(closes) - length, len(closes)):
        true_ranges.append(
            true_range(
                high=highs[idx],
                low=lows[idx],
                prev_close=closes[idx - 1] if idx > 0 else None,
            )
        )
    price = closes[-1]
    if price <= 0:
        return 0.0
    return (sum(true_ranges) / len(true_ranges)) / price * 100.0


def compute_ichimoku_snapshot(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    tenkan_length: int,
    kijun_length: int,
    senkou_b_length: int,
    displacement: int,
    rsi_length: int,
    rsi_threshold: float,
    volatility_length: int,
    volatility_gate_threshold: float,
) -> IchimokuSnapshot | None:
    """Compute a conservative recovered Ichimoku snapshot from completed bars."""
    warmup = max(senkou_b_length, displacement + 1, rsi_length + 1, volatility_length + 1)
    if len(closes) < warmup or len(highs) < warmup or len(lows) < warmup:
        return None

    tenkan = _midpoint(highs, lows, length=tenkan_length)
    kijun = _midpoint(highs, lows, length=kijun_length)
    span_a = (tenkan + kijun) / 2.0
    span_b = _midpoint(highs, lows, length=senkou_b_length)
    chikou_reference_close = closes[-(displacement + 1)]
    rsi = _rsi(closes, length=rsi_length)
    volatility_pct = _volatility_pct(highs, lows, closes, length=volatility_length)

    return IchimokuSnapshot(
        close=closes[-1],
        tenkan=tenkan,
        kijun=kijun,
        span_a=span_a,
        span_b=span_b,
        chikou_reference_close=chikou_reference_close,
        rsi=rsi,
        volatility_pct=volatility_pct,
        rsi_threshold=rsi_threshold,
        volatility_gate_threshold=volatility_gate_threshold,
    )


def decide_ichimoku_action(*, snapshot: IchimokuSnapshot, position_side: str, allow_short: bool) -> str:
    """Conservative first-pass action policy from the recovered Ichimoku rule sheet."""
    if position_side == "FLAT":
        if snapshot.long_entry_ready:
            return "ENTER_LONG"
        if allow_short and snapshot.short_entry_ready:
            return "ENTER_SHORT"
        return "HOLD"

    if position_side == "LONG":
        if allow_short and snapshot.short_entry_ready:
            return "FLIP_SHORT"
        if snapshot.long_exit_ready:
            return "EXIT"
        return "HOLD"

    if position_side == "SHORT":
        if snapshot.long_entry_ready:
            return "FLIP_LONG"
        if snapshot.short_exit_ready:
            return "EXIT"
        return "HOLD"

    raise ValueError(f"unknown position_side={position_side}")


class HLIchimoku3HStrategy(Strategy):
    """Bar-close Ichimoku strategy for a single HL perp instrument."""

    def __init__(self, config: HLIchimoku3HConfig) -> None:
        super().__init__(config)
        self._cfg = config
        window = max(
            config.senkou_b_length,
            config.displacement + 1,
            config.rsi_length + 1,
            config.volatility_length + 1,
        )
        self._highs: deque[float] = deque(maxlen=window)
        self._lows: deque[float] = deque(maxlen=window)
        self._closes: deque[float] = deque(maxlen=window)
        self._position_side: str = "FLAT"

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("HLIchimoku3HStrategy requires bar_type + instrument_id")
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

        snapshot = compute_ichimoku_snapshot(
            highs=list(self._highs),
            lows=list(self._lows),
            closes=list(self._closes),
            tenkan_length=self._cfg.tenkan_length,
            kijun_length=self._cfg.kijun_length,
            senkou_b_length=self._cfg.senkou_b_length,
            displacement=self._cfg.displacement,
            rsi_length=self._cfg.rsi_length,
            rsi_threshold=self._cfg.rsi_threshold,
            volatility_length=self._cfg.volatility_length,
            volatility_gate_threshold=self._cfg.volatility_gate_threshold,
        )
        if snapshot is None:
            return

        action = decide_ichimoku_action(
            snapshot=snapshot,
            position_side=self._position_side,
            allow_short=self._cfg.allow_short,
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
    "HLIchimoku3HConfig",
    "HLIchimoku3HStrategy",
    "IchimokuSnapshot",
    "compute_ichimoku_snapshot",
    "decide_ichimoku_action",
]
