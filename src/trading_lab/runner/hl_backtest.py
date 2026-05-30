"""
Hyperliquid perp backtest runner.

Sits alongside `runner/backtest.py` (which is Polymarket-arb-specific) and
exposes a clean entry point for bar-driven crypto perp strategies:

    runner = HyperliquidBacktestRunner(catalog, fees=...)
    result = runner.run_single(
        coin="BTC",
        bar_interval="1h",
        strategy_module="trading_lab.strategies.hl_donchian",
        strategy_class="DonchianBreakoutStrategy",
        strategy_config_class="DonchianBreakoutConfig",
        strategy_params={"channel": 24, ...},
        start=..., end=..., initial_capital_usdc=10_000.0,
    )

Per-symbol design:
  * One `BacktestEngine` per (strategy, coin, window) call. NT's Rust logger
    is process-global and only initialises once — guarded by a module flag
    so repeat calls in the same process don't panic (same trick as
    `runner/backtest.py:_NT_LOGGER_INITIALISED`).
  * MARGIN account with USDC starting balance.
  * `MakerTakerFeeModel` reads `maker_fee`/`taker_fee` from the instrument
    descriptor — set in `make_hl_perpetual`.
  * Funding PnL is computed post-engine via `research.funding` and added
    to the equity curve before metrics are computed.

Multi-market driver `run_multi_market` loops over a list of coins and
aggregates results into a portfolio-level report (equal-weighted by
default; pluggable). Survivorship is avoided because the caller passes
the universe that was top-N at the start of the window.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trading_lab.data.hl_bar_loader import load_bars, make_bar_type
from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.research.funding import (
    compute_funding_pnl,
    equity_with_funding,
)
from trading_lab.research.metrics import (
    PERIODS_PER_YEAR_BY_INTERVAL,
    PerformanceMetrics,
    combine_metrics,
)
from trading_lab.venues.hyperliquid.instruments import (
    DEFAULT_MAKER_BPS,
    DEFAULT_TAKER_BPS,
    HYPERLIQUID_VENUE,
    make_hl_perpetual,
)

log = logging.getLogger(__name__)

_NT_LOGGER_INITIALISED = False


@dataclass
class HyperliquidFeeConfig:
    maker_bps: float = DEFAULT_MAKER_BPS
    taker_bps: float = DEFAULT_TAKER_BPS
    # Optional extra slippage applied to fills in addition to the FillModel
    # default. Use to stress-test cost assumptions. Bps applied as a fraction
    # of fill price; sign relative to the order direction.
    extra_slippage_bps: float = 0.0


@dataclass
class HLMarketResult:
    coin: str
    bar_interval: str
    n_bars: int
    n_orders: int
    n_fills: int
    metrics: PerformanceMetrics
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "coin": self.coin,
            "bar_interval": self.bar_interval,
            "n_bars": self.n_bars,
            "n_orders": self.n_orders,
            "n_fills": self.n_fills,
            "metrics": self.metrics.to_dict(),
            "error": self.error,
        }


@dataclass
class HLPortfolioResult:
    per_market: list[HLMarketResult]
    portfolio_metrics: PerformanceMetrics
    portfolio_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    correlation_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_market": [m.to_dict() for m in self.per_market],
            "portfolio_metrics": self.portfolio_metrics.to_dict(),
            "correlation_matrix": (
                self.correlation_matrix.to_dict() if not self.correlation_matrix.empty else {}
            ),
            "n_markets": len(self.per_market),
            "n_markets_with_fills": sum(1 for m in self.per_market if m.n_fills > 0),
        }


class HyperliquidBacktestRunner:
    """Bar-driven backtest runner for HL perps."""

    def __init__(
        self,
        catalog: HyperliquidCatalog,
        fees: HyperliquidFeeConfig | None = None,
        log_level: str = "WARN",
    ) -> None:
        self._catalog = catalog
        self._fees = fees or HyperliquidFeeConfig()
        self._log_level = log_level

    # ------------------------------------------------------------------
    # Single-symbol
    # ------------------------------------------------------------------

    def run_single(
        self,
        *,
        coin: str,
        bar_interval: str,
        start: datetime,
        end: datetime,
        strategy_module: str,
        strategy_class: str,
        strategy_config_class: str | None = None,
        strategy_params: dict[str, Any] | None = None,
        initial_capital_usdc: float = 10_000.0,
        use_funding: bool = True,
    ) -> HLMarketResult:
        from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
        from nautilus_trader.backtest.models import FillModel
        from nautilus_trader.config import LoggingConfig
        from nautilus_trader.model.currencies import USDC
        from nautilus_trader.model.enums import AccountType, OmsType
        from nautilus_trader.model.identifiers import TraderId
        from nautilus_trader.model.objects import Money

        params = strategy_params or {}
        bars, instrument = load_bars(
            self._catalog,
            coin,
            bar_interval,
            start,
            end,
            instrument=make_hl_perpetual(
                coin,
                maker_bps=self._fees.maker_bps,
                taker_bps=self._fees.taker_bps,
            ),
        )
        if not bars:
            log.warning("hl_backtest_no_bars coin=%s interval=%s", coin, bar_interval)
            return HLMarketResult(
                coin=coin,
                bar_interval=bar_interval,
                n_bars=0,
                n_orders=0,
                n_fills=0,
                metrics=PerformanceMetrics(),
                error="no_data",
            )

        engine = _build_engine(initial_capital_usdc, instrument, self._log_level, FillModel())
        engine.add_instrument(instrument)
        engine.add_data(bars, sort=True)

        strategy = _instantiate_strategy(
            strategy_module=strategy_module,
            strategy_class=strategy_class,
            strategy_config_class=strategy_config_class,
            params=params,
            bar_type=make_bar_type(coin, bar_interval),
            instrument_id=instrument.id,
        )
        engine.add_strategy(strategy)

        try:
            engine.run()
        except Exception as exc:  # noqa: BLE001
            log.exception("hl_backtest_run_failed coin=%s", coin)
            engine.dispose()
            return HLMarketResult(
                coin=coin,
                bar_interval=bar_interval,
                n_bars=len(bars),
                n_orders=0,
                n_fills=0,
                metrics=PerformanceMetrics(),
                error=f"engine_run_failed: {exc!r}",
            )

        result = self._extract_result(
            engine=engine,
            coin=coin,
            bar_interval=bar_interval,
            instrument=instrument,
            n_bars=len(bars),
            initial_capital=initial_capital_usdc,
            window_start=start,
            window_end=end,
            use_funding=use_funding,
        )
        engine.dispose()
        return result

    # ------------------------------------------------------------------
    # Multi-symbol portfolio
    # ------------------------------------------------------------------

    def run_multi_market(
        self,
        *,
        coins: list[str],
        bar_interval: str,
        start: datetime,
        end: datetime,
        strategy_module: str,
        strategy_class: str,
        strategy_config_class: str | None = None,
        strategy_params: dict[str, Any] | None = None,
        initial_capital_usdc_per_market: float = 10_000.0,
        use_funding: bool = True,
    ) -> HLPortfolioResult:
        per_market: list[HLMarketResult] = []
        for c in coins:
            r = self.run_single(
                coin=c,
                bar_interval=bar_interval,
                start=start,
                end=end,
                strategy_module=strategy_module,
                strategy_class=strategy_class,
                strategy_config_class=strategy_config_class,
                strategy_params=strategy_params,
                initial_capital_usdc=initial_capital_usdc_per_market,
                use_funding=use_funding,
            )
            per_market.append(r)

        portfolio = _aggregate_portfolio(
            per_market,
            bar_interval=bar_interval,
            initial_capital_per_market=initial_capital_usdc_per_market,
        )
        return portfolio

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_result(
        self,
        *,
        engine,
        coin: str,
        bar_interval: str,
        instrument,
        n_bars: int,
        initial_capital: float,
        window_start: datetime,
        window_end: datetime,
        use_funding: bool,
    ) -> HLMarketResult:
        try:
            orders_df = engine.trader.generate_order_fills_report()
        except Exception:
            orders_df = pd.DataFrame()
        try:
            account_df = engine.trader.generate_account_report(HYPERLIQUID_VENUE)
        except Exception:
            account_df = pd.DataFrame()
        try:
            positions_df = engine.trader.generate_positions_report()
        except Exception:
            positions_df = pd.DataFrame()

        n_orders = int(len(orders_df)) if not orders_df.empty else 0
        n_fills = 0
        if "filled_qty" in orders_df.columns:
            n_fills = int((pd.to_numeric(orders_df["filled_qty"], errors="coerce").fillna(0) > 0).sum())

        # Equity curve from NT account report (USDC balance at each tick),
        # resampled to bar cadence so per-bar returns drive annualisation
        # in `combine_metrics`. Without this, sparse fill-time samples
        # combined with high periods_per_year inflate Sharpe in absolute
        # value (large positive or negative depending on direction).
        equity = _equity_curve_from_account(account_df, initial_capital)
        equity = _resample_equity_to_bars(equity, bar_interval, window_start, window_end)

        # Per-trade realised PnL (one entry per closed position).
        per_trade = _per_trade_pnl_from_positions(positions_df)

        # Cost drag from fills.
        fees_paid = _sum_commissions(orders_df)
        turnover = _turnover(orders_df)

        # Funding PnL.
        funding_pnl = 0.0
        if use_funding:
            position_history = _position_history_from_fills(orders_df, coin=coin)
            funding_history = self._catalog.read_funding(coin, window_start, window_end)
            mark_series = _mark_series_from_catalog(self._catalog, coin, window_start, window_end)
            fr = compute_funding_pnl(position_history, funding_history, mark_series)
            funding_pnl = fr.funding_pnl
            if not equity.empty and not fr.detail.empty:
                equity = equity_with_funding(equity, fr.detail)

        # Price PnL = total pnl - funding pnl (after fees, which NT bakes in).
        terminal = float(equity.iloc[-1]) if not equity.empty else initial_capital
        total_pnl = terminal - initial_capital
        price_pnl = total_pnl - funding_pnl

        metrics = combine_metrics(
            per_trade_pnl=per_trade,
            equity_curve=equity,
            bar_interval=bar_interval,
            initial_capital=initial_capital,
            price_pnl=price_pnl,
            funding_pnl=funding_pnl,
            fees_paid=fees_paid,
            turnover_notional=turnover,
            extras={
                "terminal_equity": terminal,
                "total_pnl": total_pnl,
                "n_positions": int(len(positions_df)) if not positions_df.empty else 0,
            },
        )
        return HLMarketResult(
            coin=coin,
            bar_interval=bar_interval,
            n_bars=n_bars,
            n_orders=n_orders,
            n_fills=n_fills,
            metrics=metrics,
            equity_curve=equity,
        )


# ---------------------------------------------------------------------------
# Engine + strategy helpers
# ---------------------------------------------------------------------------


def _build_engine(initial_capital_usdc: float, instrument, log_level: str, fill_model):
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.backtest.models import MakerTakerFeeModel
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.model.currencies import USDC
    from nautilus_trader.model.enums import AccountType, BookType, OmsType
    from nautilus_trader.model.identifiers import TraderId
    from nautilus_trader.model.objects import Money

    global _NT_LOGGER_INITIALISED
    if not _NT_LOGGER_INITIALISED:
        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id=TraderId("HL-BACKTEST-001"),
                logging=LoggingConfig(log_level=log_level),
            )
        )
        _NT_LOGGER_INITIALISED = True
    else:
        # NT's Rust logger is a process-global singleton; second engine must
        # bypass logging init to avoid the "logger already set" panic.
        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id=TraderId("HL-BACKTEST-001"),
                logging=LoggingConfig(bypass_logging=True),
            )
        )

    engine.add_venue(
        venue=HYPERLIQUID_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(initial_capital_usdc, USDC)],
        fill_model=fill_model,
        fee_model=MakerTakerFeeModel(),
        book_type=BookType.L1_MBP,
        # Bar-driven backtests don't have order book deltas; L1 mid is enough
        # for fills against the bar close.
    )
    return engine


def _instantiate_strategy(
    *,
    strategy_module: str,
    strategy_class: str,
    strategy_config_class: str | None,
    params: dict[str, Any],
    bar_type,
    instrument_id,
):
    mod = importlib.import_module(strategy_module)
    cls = getattr(mod, strategy_class)
    cfg_cls = getattr(mod, strategy_config_class) if strategy_config_class else None
    # Inject bar_type/instrument_id into params if the config exposes those fields.
    enriched = dict(params)
    if cfg_cls is not None:
        fields = _config_fields(cfg_cls)
        if "bar_type" in fields:
            enriched.setdefault("bar_type", bar_type)
        if "instrument_id" in fields:
            enriched.setdefault("instrument_id", instrument_id)
        cfg = cfg_cls(**{k: v for k, v in enriched.items() if k in fields})
        strategy = cls(config=cfg)
    else:
        strategy = cls()
    return strategy


def _config_fields(cfg_cls) -> set[str]:
    if hasattr(cfg_cls, "__struct_fields__"):
        return set(cfg_cls.__struct_fields__)
    if hasattr(cfg_cls, "model_fields"):
        return set(cfg_cls.model_fields.keys())
    return set()


# ---------------------------------------------------------------------------
# Post-run extraction helpers
# ---------------------------------------------------------------------------


def _equity_curve_from_account(account_df: pd.DataFrame, initial_capital: float) -> pd.Series:
    if account_df is None or account_df.empty:
        return pd.Series(dtype=float)
    df = account_df
    # NT's `reported=True` rows are venue-pushed account updates (just the
    # initial deposit in a backtest). `reported=False` rows are the engine's
    # post-fill / post-margin snapshots — those carry the actual equity
    # curve. Keep all rows; if both kinds exist we just use them sequentially.
    col = None
    for cand in ("total", "balance_total", "balance", "free", "account_balance"):
        if cand in df.columns:
            col = cand
            break
    if col is None:
        return pd.Series(dtype=float)
    # `total` may be a string ('9999.54 USDC' or '9999.54') — strip + numeric.
    raw_vals = df[col].map(_strip_currency)
    eq = pd.to_numeric(raw_vals, errors="coerce").dropna()
    if eq.empty:
        return pd.Series(dtype=float)

    # Index by snapshot timestamp; the account report's index IS the ts.
    idx = None
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
    else:
        for ts_col in ("ts_init", "ts_event", "timestamp"):
            if ts_col in df.columns:
                try:
                    raw = df[ts_col]
                    if pd.api.types.is_datetime64_any_dtype(raw):
                        idx = pd.to_datetime(raw, utc=True)
                    else:
                        idx = pd.to_datetime(raw, unit="ns", utc=True)
                    break
                except Exception:
                    continue
    if idx is not None:
        eq = eq.copy()
        eq.index = idx[: len(eq)]
    else:
        eq = eq.reset_index(drop=True)

    if not eq.empty and float(eq.iloc[0]) != float(initial_capital):
        first_idx = eq.index[0]
        if isinstance(first_idx, pd.Timestamp):
            anchor = first_idx - pd.Timedelta(seconds=1)
        else:
            anchor = -1
        eq = pd.concat([pd.Series([initial_capital], index=[anchor]), eq])
    eq = eq.sort_index()
    return eq.astype(float)


def _per_trade_pnl_from_positions(positions_df: pd.DataFrame) -> list[float]:
    """
    Realised PnL series from NT's positions report — one entry per closed
    position. NT tracks open/close, FIFO matching, fees, and realised_pnl
    for us, which is much cleaner than reconstructing from orders.
    Open positions at end-of-window are excluded (their PnL is unrealised
    and already reflected in the equity curve).
    """
    if positions_df is None or positions_df.empty:
        return []
    if "realized_pnl" not in positions_df.columns:
        return []
    closed = positions_df.copy()
    if "ts_closed" in closed.columns:
        closed = closed[closed["ts_closed"].notna()]
    # Note: NT marks closed-position rows with is_snapshot=True (the row IS
    # the final snapshot of that position). Don't filter on is_snapshot.
    if closed.empty:
        return []
    return [
        float(_strip_currency(v))
        for v in closed["realized_pnl"].tolist()
        if v is not None
    ]


_BAR_FREQ: dict[str, str] = {
    "1m": "1min", "5m": "5min", "15m": "15min",
    "1h": "1h", "4h": "4h", "1d": "1D",
}


def _resample_equity_to_bars(
    equity: pd.Series,
    bar_interval: str,
    window_start: datetime,
    window_end: datetime,
) -> pd.Series:
    """Reindex equity onto a regular bar grid so annualisation matches."""
    if equity.empty:
        return equity
    freq = _BAR_FREQ.get(bar_interval, "1h")
    if not isinstance(equity.index, pd.DatetimeIndex):
        return equity
    # Build the bar grid clipped to actual range present.
    ws = pd.Timestamp(window_start)
    we = pd.Timestamp(window_end)
    if ws.tzinfo is None:
        ws = ws.tz_localize("UTC")
    else:
        ws = ws.tz_convert("UTC")
    if we.tzinfo is None:
        we = we.tz_localize("UTC")
    else:
        we = we.tz_convert("UTC")
    start = max(ws, equity.index[0])
    end = min(we, equity.index[-1])
    if end <= start:
        return equity
    grid = pd.date_range(start=start, end=end, freq=freq, tz="UTC")
    # Dedupe duplicate index entries (NT can emit ts collisions).
    eq = equity[~equity.index.duplicated(keep="last")].sort_index()
    out = eq.reindex(eq.index.union(grid)).sort_index().ffill().reindex(grid).ffill().bfill()
    return out.astype(float)


def _strip_currency(v: Any) -> float:
    """NT reports often pack 'value CCY' strings; strip the currency suffix."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    parts = s.split()
    try:
        return float(parts[0])
    except ValueError:
        return 0.0


def _position_history_from_fills(orders_df: pd.DataFrame, coin: str) -> pd.DataFrame:
    """Emit signed-qty snapshots after every fill (for funding accrual)."""
    if orders_df is None or orders_df.empty:
        return pd.DataFrame(columns=["ts_ms", "coin", "qty"])
    df = orders_df.copy()
    df["filled_qty"] = pd.to_numeric(df["filled_qty"], errors="coerce").fillna(0)
    df = df[df["filled_qty"] > 0]
    if df.empty:
        return pd.DataFrame(columns=["ts_ms", "coin", "qty"])
    side_col = "side" if "side" in df.columns else "order_side"
    if side_col not in df.columns:
        return pd.DataFrame(columns=["ts_ms", "coin", "qty"])
    ts_col = None
    for c in ("ts_init", "ts_event", "ts_last", "timestamp"):
        if c in df.columns:
            ts_col = c
            break
    if ts_col is None:
        return pd.DataFrame(columns=["ts_ms", "coin", "qty"])
    df = df.sort_values(ts_col)

    pos = 0.0
    rows: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        side = str(getattr(row, side_col, "")).upper()
        qty = float(row.filled_qty) * (1.0 if side == "BUY" else -1.0)
        pos += qty
        ts_raw = getattr(row, ts_col)
        ts_ms = _to_ms(ts_raw)
        rows.append({"ts_ms": ts_ms, "coin": coin, "qty": pos})
    return pd.DataFrame(rows)


def _to_ms(ts: Any) -> int:
    """Normalize NT report timestamps (Timestamp or int ns) to int ms."""
    if isinstance(ts, pd.Timestamp):
        return int(ts.value // 1_000_000)
    try:
        n = int(ts)
    except (TypeError, ValueError):
        return 0
    # Heuristic: ns vs ms vs s based on magnitude.
    if n > 1_000_000_000_000_000_000:  # ns
        return n // 1_000_000
    if n > 1_000_000_000_000:  # ms
        return n
    return n * 1000  # s


def _sum_commissions(orders_df: pd.DataFrame) -> float:
    """NT stores commissions as a list of strings like `['0.45 USDC']` per fill."""
    if orders_df is None or orders_df.empty:
        return 0.0
    col = None
    for cand in ("commission", "commissions", "fees"):
        if cand in orders_df.columns:
            col = cand
            break
    if col is None:
        return 0.0
    total = 0.0
    for entry in orders_df[col]:
        if entry is None:
            continue
        if isinstance(entry, (list, tuple)):
            for item in entry:
                total += _strip_currency(item)
        else:
            total += _strip_currency(entry)
    return total


def _turnover(orders_df: pd.DataFrame) -> float:
    if orders_df is None or orders_df.empty:
        return 0.0
    if "filled_qty" not in orders_df.columns or "avg_px" not in orders_df.columns:
        return 0.0
    q = pd.to_numeric(orders_df["filled_qty"], errors="coerce").fillna(0)
    p = pd.to_numeric(orders_df["avg_px"], errors="coerce").fillna(0)
    return float((q * p).abs().sum())


def _mark_series_from_catalog(
    catalog: HyperliquidCatalog,
    coin: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    df = catalog.read_candles(coin, "1h", start, end)
    if df.empty:
        df = catalog.read_candles(coin, "5m", start, end)
    if df.empty:
        return pd.DataFrame(columns=["ts_ms", "mark_px"])
    out = pd.DataFrame(
        {
            "ts_ms": df["ts_close_ms"].astype("int64"),
            "mark_px": df["close"].astype(float),
        }
    )
    return out


# ---------------------------------------------------------------------------
# Portfolio aggregation
# ---------------------------------------------------------------------------


def _aggregate_portfolio(
    per_market: list[HLMarketResult],
    bar_interval: str,
    initial_capital_per_market: float,
) -> HLPortfolioResult:
    valid = [m for m in per_market if not m.equity_curve.empty]
    if not valid:
        return HLPortfolioResult(
            per_market=per_market,
            portfolio_metrics=PerformanceMetrics(),
        )

    # Per-market equity curves can carry duplicate timestamps (NT may emit
    # multiple account snapshots at the same ns when both a margin update
    # and a fill land in the same bar). Take the last value at each ts
    # before concat so the index stays unique.
    series_by_coin = {}
    for m in valid:
        ser = m.equity_curve.copy()
        ser = ser[~ser.index.duplicated(keep="last")]
        ser = ser.sort_index()
        series_by_coin[m.coin] = ser

    aligned = pd.concat(series_by_coin, axis=1).sort_index().ffill()
    aligned = aligned.fillna(initial_capital_per_market)

    # Equal-weight: portfolio = mean of per-market equity (already per-capital).
    portfolio_equity = aligned.mean(axis=1)

    # Returns correlation: drop the initial-capital row so we don't deflate.
    pct = aligned.pct_change().dropna(how="all")
    corr = pct.corr() if not pct.empty else pd.DataFrame()

    all_trades: list[float] = []
    total_funding = 0.0
    total_fees = 0.0
    total_turnover = 0.0
    total_price = 0.0
    for m in valid:
        # Recompute per_trade from extras is unavailable here; use win/loss
        # decomposition by recomputing from `metrics` is lossy. Accept the
        # trade-level rollup as sum of (trade_sharpe ignored) and just stick
        # equity-level numbers. We do roll up funding/fees/turnover/price PnL.
        total_funding += m.metrics.funding_pnl
        total_fees += m.metrics.fees_paid
        total_turnover += m.metrics.turnover_notional
        total_price += m.metrics.price_pnl
        # Estimate trade pnls from win_rate × expectancy reconstruction —
        # cheap approximation. Backtest callers wanting exact trade-Sharpe
        # should compute per-market.
        n = m.metrics.n_trades
        if n > 0 and m.metrics.expectancy != 0:
            # Distribute as n copies of expectancy. Not exact, but stable.
            all_trades.extend([m.metrics.expectancy] * n)

    # Portfolio total pnl = terminal equity - initial. Equal-weight aggregation.
    terminal = float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else initial_capital_per_market
    total_pnl = (terminal - initial_capital_per_market) * len(valid)
    portfolio_metrics = combine_metrics(
        per_trade_pnl=all_trades,
        equity_curve=portfolio_equity,
        bar_interval=bar_interval,
        initial_capital=initial_capital_per_market,
        price_pnl=total_price,
        funding_pnl=total_funding,
        fees_paid=total_fees,
        turnover_notional=total_turnover,
        extras={
            "n_markets": len(valid),
            "n_markets_total": len(per_market),
            "mean_per_market_sharpe": float(
                np.mean([m.metrics.sharpe for m in valid]) if valid else 0.0
            ),
            "terminal_equity": terminal,
            "total_pnl": total_pnl,
        },
    )
    return HLPortfolioResult(
        per_market=per_market,
        portfolio_metrics=portfolio_metrics,
        portfolio_equity=portfolio_equity,
        correlation_matrix=corr,
    )


__all__ = [
    "HLMarketResult",
    "HLPortfolioResult",
    "HyperliquidBacktestRunner",
    "HyperliquidFeeConfig",
]
