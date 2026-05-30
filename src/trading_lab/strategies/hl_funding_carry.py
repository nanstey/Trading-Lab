"""
Funding-rate carry for HL perps.

Perpetuals charge funding hourly: positive funding means longs pay shorts,
negative means shorts pay longs. A classic carry trade is to take the
side that *receives* funding and hold while the rate stays attractive.

Signal here is bar-driven (1h aligns with HL's funding cadence): we look
at the most recent funding rate from the bar's metadata window. If the
absolute rate exceeds `entry_threshold_bps`, take the receiving side; exit
when the rate falls back below `exit_threshold_bps`.

We use the catalog's funding history (loaded via a small reader at strategy
start) keyed by timestamp. Bar handler does an O(log n) bisect into the
sorted ts array to find the most recent funding stamp.

Risk: position is exposed to price moves, not just funding. Mitigations:
  * stop_loss_pct  : hard stop on adverse price move from entry
  * max_hold_bars  : forced exit after N bars regardless
  * skip_high_vol  : skip new entries when trailing ATR exceeds threshold
"""

from __future__ import annotations

import bisect
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class FundingCarryConfig(StrategyConfig, frozen=True):
    strategy_id: str = "HL-FUNDING-CARRY-001"
    instrument_id: InstrumentId | None = None
    bar_type: BarType | None = None
    coin: str = ""
    data_dir: str = "data/parquet"

    entry_threshold_bps: float = 1.0     # 1 bps/hour = ~8.76% APR
    exit_threshold_bps: float = 0.5
    notional_usdc: float = 1000.0
    stop_loss_pct: float = 2.5           # % adverse price move
    max_hold_bars: int = 48              # force-exit cap
    atr_lookback: int = 24               # bars
    atr_skip_pct: float = 5.0            # skip entries when ATR > 5% of price


class FundingCarryStrategy(Strategy):
    def __init__(self, config: FundingCarryConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._side: str = "FLAT"
        self._entry_px: float = 0.0
        self._held_bars: int = 0
        self._tr: deque[float] = deque(maxlen=config.atr_lookback)
        self._prev_close: float | None = None
        self._funding_ts_ms: list[int] = []
        self._funding_rates: list[float] = []

    def on_start(self) -> None:
        if self._cfg.bar_type is None or self._cfg.instrument_id is None:
            raise RuntimeError("FundingCarryStrategy requires bar_type + instrument_id")
        if not self._cfg.coin:
            raise RuntimeError("FundingCarryStrategy requires coin (for funding lookup)")
        self.subscribe_bars(self._cfg.bar_type)
        self._load_funding()

    def on_stop(self) -> None:
        try:
            self.close_all_positions(self._cfg.instrument_id)
        except Exception:
            pass

    def _load_funding(self) -> None:
        # Local import to avoid pulling pandas at module import.
        from trading_lab.data.hl_catalog import HyperliquidCatalog

        cat = HyperliquidCatalog(Path(self._cfg.data_dir))
        df = cat.read_funding(
            self._cfg.coin,
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2100, 1, 1, tzinfo=UTC),
        )
        if df.empty:
            return
        self._funding_ts_ms = df["ts_ms"].astype("int64").tolist()
        self._funding_rates = df["funding_rate"].astype(float).tolist()

    def _latest_funding(self, bar_ts_ms: int) -> float:
        if not self._funding_ts_ms:
            return 0.0
        i = bisect.bisect_right(self._funding_ts_ms, bar_ts_ms) - 1
        if i < 0:
            return 0.0
        return self._funding_rates[i]

    def on_bar(self, bar: Bar) -> None:
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        if self._prev_close is not None:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
            self._tr.append(tr)
        self._prev_close = close

        bar_ts_ms = int(bar.ts_event) // 1_000_000
        funding = self._latest_funding(bar_ts_ms)
        # Funding is per-hour as a fraction (e.g. 0.0001 = 1 bps).
        funding_bps = funding * 1e4

        instrument = self.cache.instrument(self._cfg.instrument_id)
        if instrument is None:
            return
        size = Quantity(max(self._cfg.notional_usdc / max(close, 1e-9), 0), instrument.size_precision)
        if float(size) <= 0:
            return

        atr_pct = (sum(self._tr) / len(self._tr) / close * 100.0) if self._tr else 0.0

        if self._side == "FLAT":
            if atr_pct > self._cfg.atr_skip_pct:
                return
            # Positive funding -> longs pay -> we want to be SHORT to receive.
            if funding_bps >= self._cfg.entry_threshold_bps:
                self._send(OrderSide.SELL, size)
                self._side = "SHORT"
                self._entry_px = close
                self._held_bars = 0
            elif funding_bps <= -self._cfg.entry_threshold_bps:
                self._send(OrderSide.BUY, size)
                self._side = "LONG"
                self._entry_px = close
                self._held_bars = 0
        else:
            self._held_bars += 1
            adverse_pct = ((close - self._entry_px) / self._entry_px * 100.0) * (
                1.0 if self._side == "SHORT" else -1.0
            )
            stale = self._held_bars >= self._cfg.max_hold_bars
            stop = adverse_pct >= self._cfg.stop_loss_pct
            if self._side == "SHORT":
                rate_decayed = funding_bps <= self._cfg.exit_threshold_bps
                if rate_decayed or stale or stop:
                    self._send(OrderSide.BUY, size)
                    self._side = "FLAT"
            else:  # LONG
                rate_decayed = funding_bps >= -self._cfg.exit_threshold_bps
                if rate_decayed or stale or stop:
                    self._send(OrderSide.SELL, size)
                    self._side = "FLAT"

    def _send(self, side: OrderSide, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)


__all__ = ["FundingCarryConfig", "FundingCarryStrategy"]
