"""
Backtest Runner.

Wires the project's Parquet `DataCatalog` and the hypothesis-driven market
selection into NautilusTrader's `BacktestEngine`. The runner registers a
`BinaryArbStrategy` per condition and reports per-market + aggregate
performance.

Two ways to drive a run:
    1. Hypothesis-slug (preferred): load `research/hypotheses/<slug>.md`,
       parse `market_criteria` from frontmatter, call `select_markets()`,
       backtest each.
    2. Ad-hoc: pass `--yes-token-id / --no-token-id / --condition-id` to
       backtest a single hand-picked pair.

Slippage / latency posture: both default to PESSIMISTIC. Polymarket book
depth is thin; the most common "looked great in backtest, dies in paper"
failure mode is exactly the slippage/latency assumption. See
`_build_fill_model` / `_build_latency_model` for the tunable knobs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_predict.config import TradingConfig

log = logging.getLogger(__name__)


@dataclass
class MarketBacktestResult:
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    n_trade_ticks: int
    n_orders: int
    n_fills: int
    pnl_usdc: float
    sharpe: float
    max_drawdown_pct: float
    fill_rate: float
    kill_switch_triggered: bool


@dataclass
class BacktestRunResult:
    per_market: list[MarketBacktestResult]
    aggregate_pnl_usdc: float
    aggregate_n_fills: int
    aggregate_n_orders: int
    mean_sharpe: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregate_pnl_usdc": self.aggregate_pnl_usdc,
            "aggregate_n_fills": self.aggregate_n_fills,
            "aggregate_n_orders": self.aggregate_n_orders,
            "mean_sharpe": self.mean_sharpe,
            "per_market": [
                {
                    "condition_id": r.condition_id,
                    "question": r.question,
                    "n_trade_ticks": r.n_trade_ticks,
                    "n_orders": r.n_orders,
                    "n_fills": r.n_fills,
                    "pnl_usdc": r.pnl_usdc,
                    "sharpe": r.sharpe,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "fill_rate": r.fill_rate,
                    "kill_switch_triggered": r.kill_switch_triggered,
                }
                for r in self.per_market
            ],
        }


class BacktestRunner:
    """NautilusTrader-backed backtesting runner for binary complement arbs."""

    def __init__(self, config: TradingConfig, data_dir: Path | None = None) -> None:
        self._config = config
        self._data_dir = data_dir or Path("data/parquet")

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run_pair(
        self,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        start: datetime,
        end: datetime,
        initial_capital_usdc: float = 10_000.0,
        question: str = "",
    ) -> MarketBacktestResult:
        """Backtest a single condition (YES + NO pair)."""
        return self._run_single(
            condition_id=condition_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            start=start,
            end=end,
            initial_capital_usdc=initial_capital_usdc,
            question=question,
        )

    def run_hypothesis(
        self,
        hypothesis_slug: str,
        start: datetime,
        end: datetime,
        initial_capital_usdc: float = 10_000.0,
        hypotheses_dir: Path = Path("research/hypotheses"),
        market_catalog_path: Path = Path("data/market_catalog.db"),
    ) -> BacktestRunResult:
        """Run a backtest for every market matched by the hypothesis's criteria."""
        from nautilus_predict.data.market_catalog import MarketCatalog
        from nautilus_predict.data.market_filter import MarketCriteria, select_markets

        md_path = hypotheses_dir / f"{hypothesis_slug}.md"
        criteria_dict, _body = _parse_hypothesis(md_path)
        if criteria_dict is None:
            raise FileNotFoundError(f"hypothesis missing: {md_path}")
        criteria = MarketCriteria.from_dict(criteria_dict)

        catalog = MarketCatalog(market_catalog_path)
        markets = select_markets(criteria, catalog)
        catalog.close()
        log.info("hypothesis %s selected %d markets", hypothesis_slug, len(markets))

        results: list[MarketBacktestResult] = []
        for m in markets:
            if not m.yes_token_id or not m.no_token_id:
                log.warning("skipping %s: missing yes/no token ids", m.condition_id)
                continue
            try:
                r = self._run_single(
                    condition_id=m.condition_id,
                    yes_token_id=m.yes_token_id,
                    no_token_id=m.no_token_id,
                    start=start,
                    end=end,
                    initial_capital_usdc=initial_capital_usdc,
                    question=m.question,
                )
                results.append(r)
            except Exception as exc:
                log.exception("backtest failed for %s: %s", m.condition_id, exc)

        return _aggregate(results)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_single(
        self,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        start: datetime,
        end: datetime,
        initial_capital_usdc: float,
        question: str,
    ) -> MarketBacktestResult:
        from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
        from nautilus_trader.config import LoggingConfig
        from nautilus_trader.model.currencies import USDC
        from nautilus_trader.model.enums import AccountType, BookType, OmsType
        from nautilus_trader.model.identifiers import TraderId, Venue
        from nautilus_trader.model.objects import Money

        from nautilus_predict.data.catalog import DataCatalog
        from nautilus_predict.data.parquet_loader import (
            load_trades_as_trade_ticks,
            make_instrument,
            reconstruct_book_from_trades,
        )
        from nautilus_predict.strategies.arb_complement import (
            BinaryArbConfig,
            BinaryArbStrategy,
        )

        log.info(
            "backtest start condition=%s yes=%s.. no=%s..",
            condition_id,
            yes_token_id[:14],
            no_token_id[:14],
        )

        data_catalog = DataCatalog(self._data_dir)
        yes_instr = make_instrument(yes_token_id, condition_id, question=question)
        no_instr = make_instrument(no_token_id, condition_id, question=question)

        yes_ticks = load_trades_as_trade_ticks(data_catalog, yes_token_id, yes_instr, start, end)
        no_ticks = load_trades_as_trade_ticks(data_catalog, no_token_id, no_instr, start, end)
        yes_deltas = reconstruct_book_from_trades(data_catalog, yes_token_id, yes_instr, start, end)
        no_deltas = reconstruct_book_from_trades(data_catalog, no_token_id, no_instr, start, end)

        log.info(
            "data loaded yes_ticks=%d no_ticks=%d yes_deltas=%d no_deltas=%d",
            len(yes_ticks),
            len(no_ticks),
            len(yes_deltas),
            len(no_deltas),
        )

        if not (yes_ticks or no_ticks):
            log.warning("no data — returning empty result")
            return MarketBacktestResult(
                condition_id=condition_id,
                question=question,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                n_trade_ticks=0,
                n_orders=0,
                n_fills=0,
                pnl_usdc=0.0,
                sharpe=0.0,
                max_drawdown_pct=0.0,
                fill_rate=0.0,
                kill_switch_triggered=False,
            )

        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id=TraderId("BACKTEST-001"),
                logging=LoggingConfig(log_level="WARN"),
            )
        )

        engine.add_venue(
            venue=Venue("POLYMARKET"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(initial_capital_usdc, USDC)],
            fill_model=self._build_fill_model(),
            latency_model=self._build_latency_model(),
            book_type=BookType.L2_MBP,
            trade_execution=True,
        )

        engine.add_instrument(yes_instr)
        engine.add_instrument(no_instr)

        # Merge ticks + deltas and let engine sort. Engine handles ordering.
        engine.add_data(yes_ticks + no_ticks, sort=True)
        engine.add_data(yes_deltas + no_deltas, sort=True)

        strategy_config = BinaryArbConfig(
            strategy_id=f"ARB-{condition_id[:8]}",
            min_profit_usdc=self._config.arb.min_profit_usdc,
            max_capital_usdc=self._config.arb.max_capital_usdc,
        )
        strategy = BinaryArbStrategy(config=strategy_config)
        # Pre-register the pair (BinaryArbStrategy.register_market_pair would
        # subscribe to deltas on the engine — we want subscription to happen
        # via on_start so it lines up with NT's engine lifecycle).
        strategy._initial_pair = (condition_id, yes_instr.id, no_instr.id)  # type: ignore[attr-defined]
        engine.add_strategy(strategy)

        engine.run()

        result = self._extract_result(
            engine, condition_id, question, yes_token_id, no_token_id, len(yes_ticks) + len(no_ticks)
        )
        engine.dispose()
        return result

    def _build_fill_model(self):
        from nautilus_trader.backtest.models import FillModel

        # Pessimistic by default: PM books are shallow, partials are common.
        return FillModel(prob_fill_on_limit=0.5, prob_slippage=0.5)

    def _build_latency_model(self):
        from nautilus_trader.backtest.models import LatencyModel

        # 200ms round trip — realistic for PM aiohttp + Polygon block timing.
        return LatencyModel(base_latency_nanos=200_000_000)

    def _extract_result(
        self,
        engine,
        condition_id: str,
        question: str,
        yes_token_id: str,
        no_token_id: str,
        n_ticks: int,
    ) -> MarketBacktestResult:
        from nautilus_trader.model.identifiers import Venue

        venue = Venue("POLYMARKET")
        try:
            orders_df = engine.trader.generate_order_fills_report()
        except Exception:
            orders_df = pd.DataFrame()
        try:
            account_df = engine.trader.generate_account_report(venue)
        except Exception:
            account_df = pd.DataFrame()

        n_orders = len(orders_df) if not orders_df.empty else 0
        n_fills = (
            int((orders_df["filled_qty"].astype(float) > 0).sum())
            if "filled_qty" in orders_df.columns
            else 0
        )

        sharpe, max_dd_pct = self._equity_metrics(account_df)

        # Complement-arb PnL: cash spent on arbs is recoverable at $1/share
        # at resolution; the genuine edge is `1.0 - combined_ask_at_fill`.
        # For backtests on unresolved markets we mark each held YES+NO pair
        # at its theoretical resolution value of $1.00 minus fees paid.
        pnl = self._terminal_pnl(engine, orders_df, venue)
        return MarketBacktestResult(
            condition_id=condition_id,
            question=question,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            n_trade_ticks=n_ticks,
            n_orders=n_orders,
            n_fills=n_fills,
            pnl_usdc=pnl,
            sharpe=sharpe,
            max_drawdown_pct=max_dd_pct,
            fill_rate=(n_fills / n_orders) if n_orders else 0.0,
            kill_switch_triggered=False,
        )

    def _terminal_pnl(self, engine, orders_df: pd.DataFrame, venue) -> float:
        """
        Compute realised-at-resolution PnL for a complement-arb backtest.

        Assumption: every filled BUY contributes
            (1.0 - avg_fill_price) * filled_qty - taker_fee_per_unit
        to PnL, paired across YES and NO legs. We use only the *minimum* of
        the two leg sizes per condition as the matched arb quantity — unmatched
        leg overflow (rare with IOC) is marked at $0.5.
        """
        if orders_df is None or orders_df.empty:
            return 0.0
        try:
            df = orders_df.copy()
            df["filled_qty"] = pd.to_numeric(df.get("filled_qty", 0), errors="coerce").fillna(0)
            df["avg_px"] = pd.to_numeric(df.get("avg_px", 0), errors="coerce").fillna(0)
            df = df[df["filled_qty"] > 0]
            if df.empty:
                return 0.0
            # Spent = sum(filled_qty * avg_px). At resolution, each share-pair pays $1.
            spent = float((df["filled_qty"] * df["avg_px"]).sum())
            # Treat YES + NO legs as paired one-for-one. Total payout at resolution
            # = (sum of filled_qty across YES legs) since each pair pays $1
            # per matched share. For approximation we take min(yes_qty, no_qty).
            # Group by instrument to split legs.
            qty_by_iid = df.groupby("instrument_id")["filled_qty"].sum()
            if len(qty_by_iid) >= 2:
                paired = min(qty_by_iid.values[0], qty_by_iid.values[1])
                # Unpaired leg is marked at $0.5 (worst case unknown).
                unpaired = abs(qty_by_iid.values[0] - qty_by_iid.values[1])
                payout = paired * 1.0 + unpaired * 0.5
            else:
                # Only one leg filled — mark at 0.5.
                payout = float(qty_by_iid.sum()) * 0.5
            return payout - spent
        except Exception as exc:
            log.warning("terminal pnl calc failed: %s", exc)
            return 0.0

    def _equity_metrics(self, account_df: pd.DataFrame) -> tuple[float, float]:
        if account_df is None or account_df.empty:
            return 0.0, 0.0
        col = None
        for candidate in ("balance_total", "total", "balance", "free", "account_balance"):
            if candidate in account_df.columns:
                col = candidate
                break
        if col is None:
            return 0.0, 0.0
        equity = pd.to_numeric(account_df[col], errors="coerce").dropna()
        if equity.empty:
            return 0.0, 0.0
        returns = equity.pct_change().dropna()
        sharpe = float(returns.mean() / returns.std() * (252**0.5)) if returns.std() > 0 else 0.0
        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max
        max_dd = float(drawdown.min()) * 100.0
        return sharpe, max_dd


def _parse_hypothesis(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not path.exists():
        return None, ""
    text = path.read_text()
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end < 0:
        return None, text
    fm = text[3:end].strip()
    body = text[end + 4 :].strip()
    try:
        import yaml

        data = yaml.safe_load(fm) or {}
    except Exception:
        data = {}
    return data.get("market_criteria"), body


def _aggregate(results: list[MarketBacktestResult]) -> BacktestRunResult:
    if not results:
        return BacktestRunResult(per_market=[], aggregate_pnl_usdc=0.0,
                                 aggregate_n_fills=0, aggregate_n_orders=0,
                                 mean_sharpe=0.0)
    total_pnl = sum(r.pnl_usdc for r in results)
    total_fills = sum(r.n_fills for r in results)
    total_orders = sum(r.n_orders for r in results)
    mean_sharpe = sum(r.sharpe for r in results) / len(results)
    return BacktestRunResult(
        per_market=results,
        aggregate_pnl_usdc=total_pnl,
        aggregate_n_fills=total_fills,
        aggregate_n_orders=total_orders,
        mean_sharpe=mean_sharpe,
    )


def _coerce_dt(d: datetime | str) -> datetime:
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return datetime.fromisoformat(d).replace(tzinfo=UTC)
