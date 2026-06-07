"""Hyperliquid BTC/ETH Long v1 clone for AlphaInsider / TradingView research."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from trading_lab.strategies.hl_signal_ops import (
    atr_pct,
    crossunder,
    exponential_moving_average,
    is_rising,
    simple_moving_average,
    true_range,
)


class HLBTCEthLongV1Config(StrategyConfig, frozen=True):
    strategy_id: str = "HL-BTC-ETH-LONG-V1-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None

    ema_length: int = 20
    sma_length: int = 100
    slow_sma_length: int = 200
    atr_length: int = 14
    macd_fast_length: int = 12
    macd_slow_length: int = 26
    macd_signal_length: int = 7
    volatility_cap_pct: float = 2.0
    stop_loss_pct: float = 1.5
    notional_usdc: float = 1_000.0


@dataclass(frozen=True)
class BTCEthLongSnapshot:
    close: float
    ema: float
    prev_ema: float
    sma: float
    prev_sma: float
    slow_sma: float
    prev_slow_sma: float
    macd_line: float
    prev_macd_line: float
    signal_line: float
    volatility_pct: float
    volatility_cap_pct: float

    @property
    def volatility_ok(self) -> bool:
        return self.volatility_pct < self.volatility_cap_pct

    @property
    def trend_ready(self) -> bool:
        return is_rising((self.prev_slow_sma, self.slow_sma)) and is_rising(
            (self.prev_sma, self.sma)
        ) and is_rising((self.prev_ema, self.ema)) and is_rising(
            (self.prev_macd_line, self.macd_line)
        )

    @property
    def price_filter_ok(self) -> bool:
        return self.ema > self.sma and self.close > self.sma

    @property
    def entry_ready(self) -> bool:
        return self.trend_ready and self.price_filter_ok and self.volatility_ok


def _ema_at(values: list[float], *, length: int, upto: int) -> float:
    return exponential_moving_average(values[: upto + 1], length=length)


def _sma_at(values: list[float], *, length: int, upto: int) -> float:
    return simple_moving_average(values[: upto + 1], length=length)


def _macd_line_at(
    values: list[float], *, fast_length: int, slow_length: int, upto: int
) -> float:
    return _ema_at(values, length=fast_length, upto=upto) - _ema_at(
        values, length=slow_length, upto=upto
    )


def _macd_signal_at(
    values: list[float], *, fast_length: int, slow_length: int, signal_length: int, upto: int
) -> float:
    start = slow_length - 1
    macd_series = [
        _macd_line_at(values, fast_length=fast_length, slow_length=slow_length, upto=idx)
        for idx in range(start, upto + 1)
    ]
    return exponential_moving_average(macd_series, length=signal_length)


def compute_btc_eth_long_snapshot(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    ema_length: int,
    sma_length: int,
    slow_sma_length: int,
    atr_length: int,
    macd_fast_length: int,
    macd_slow_length: int,
    macd_signal_length: int,
    volatility_cap_pct: float,
) -> BTCEthLongSnapshot | None:
    """Compute the recovered BTC/ETH Long v1 filters from completed-bar history."""

    warmup = max(
        slow_sma_length + 1,
        macd_slow_length + macd_signal_length,
        atr_length + 1,
    )
    if len(closes) < warmup or len(highs) < warmup or len(lows) < warmup:
        return None

    curr_idx = len(closes) - 1
    prev_idx = curr_idx - 1

    ema = _ema_at(closes, length=ema_length, upto=curr_idx)
    prev_ema = _ema_at(closes, length=ema_length, upto=prev_idx)
    sma = _sma_at(closes, length=sma_length, upto=curr_idx)
    prev_sma = _sma_at(closes, length=sma_length, upto=prev_idx)
    slow_sma = _sma_at(closes, length=slow_sma_length, upto=curr_idx)
    prev_slow_sma = _sma_at(closes, length=slow_sma_length, upto=prev_idx)
    macd_line = _macd_line_at(
        closes,
        fast_length=macd_fast_length,
        slow_length=macd_slow_length,
        upto=curr_idx,
    )
    prev_macd_line = _macd_line_at(
        closes,
        fast_length=macd_fast_length,
        slow_length=macd_slow_length,
        upto=prev_idx,
    )
    signal_line = _macd_signal_at(
        closes,
        fast_length=macd_fast_length,
        slow_length=macd_slow_length,
        signal_length=macd_signal_length,
        upto=curr_idx,
    )

    true_ranges = []
    for idx in range(len(closes) - atr_length, len(closes)):
        true_ranges.append(
            true_range(
                high=highs[idx],
                low=lows[idx],
                prev_close=closes[idx - 1] if idx > 0 else None,
            )
        )
    volatility_pct = atr_pct(true_ranges, price=closes[-1])

    snapshot = BTCEthLongSnapshot(
        close=closes[-1],
        ema=ema,
        prev_ema=prev_ema,
        sma=sma,
        prev_sma=prev_sma,
        slow_sma=slow_sma,
        prev_slow_sma=prev_slow_sma,
        macd_line=macd_line,
        prev_macd_line=prev_macd_line,
        signal_line=signal_line,
        volatility_pct=volatility_pct,
        volatility_cap_pct=volatility_cap_pct,
    )
    return snapshot


def decide_btc_eth_long_action(
    *,
    snapshot: BTCEthLongSnapshot,
    position_side: str,
    entry_price: float | None,
    stop_loss_pct: float,
) -> str:
    """Conservative first-pass policy for the recovered long-only strategy."""
    if position_side == "FLAT":
        return "ENTER_LONG" if snapshot.entry_ready else "HOLD"

    if position_side != "LONG":
        raise ValueError(f"unknown position_side={position_side}")

    if entry_price is not None and snapshot.close <= entry_price * (1.0 - (stop_loss_pct / 100.0)):
        return "EXIT"
    if crossunder(
        prev_left=snapshot.prev_ema,
        prev_right=snapshot.prev_sma,
        curr_left=snapshot.ema,
        curr_right=snapshot.sma,
    ):
        return "EXIT"
    return "HOLD"


class HLBTCEthLongV1Strategy(Strategy):
    """Bar-close long-only trend filter strategy for a single HL perp instrument."""

    def __init__(self, config: HLBTCEthLongV1Config) -> None:
        super().__init__(config)
        self._cfg = config
        window = max(config.slow_sma_length + 1, config.macd_slow_length + config.macd_signal_length)
        self._highs: deque[float] = deque(maxlen=window)
        self._lows: deque[float] = deque(maxlen=window)
        self._closes: deque[float] = deque(maxlen=window)
        self._position_side: str = "FLAT"
        self._entry_price: float | None = None

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("HLBTCEthLongV1Strategy requires bar_type + instrument_id")
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

        snapshot = compute_btc_eth_long_snapshot(
            highs=list(self._highs),
            lows=list(self._lows),
            closes=list(self._closes),
            ema_length=self._cfg.ema_length,
            sma_length=self._cfg.sma_length,
            slow_sma_length=self._cfg.slow_sma_length,
            atr_length=self._cfg.atr_length,
            macd_fast_length=self._cfg.macd_fast_length,
            macd_slow_length=self._cfg.macd_slow_length,
            macd_signal_length=self._cfg.macd_signal_length,
            volatility_cap_pct=self._cfg.volatility_cap_pct,
        )
        if snapshot is None:
            return

        action = decide_btc_eth_long_action(
            snapshot=snapshot,
            position_side=self._position_side,
            entry_price=self._entry_price,
            stop_loss_pct=self._cfg.stop_loss_pct,
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
            self._entry_price = close
        elif action == "EXIT":
            self._send(OrderSide.SELL, size)
            self._position_side = "FLAT"
            self._entry_price = None

    def _send(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)


__all__ = [
    "BTCEthLongSnapshot",
    "compute_btc_eth_long_snapshot",
    "decide_btc_eth_long_action",
    "HLBTCEthLongV1Config",
    "HLBTCEthLongV1Strategy",
]
