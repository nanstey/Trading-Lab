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

from trading_lab.config import TradingConfig
from trading_lab.data.parquet_loader import (
    load_book_as_order_book_deltas,
    load_trades_as_trade_ticks,
    make_instrument,
    reconstruct_book_from_trades,
)

log = logging.getLogger(__name__)

# Process-global guard for NT's Rust logger (panics on re-init).
_NT_LOGGER_INITIALISED = False


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
    n_pair_trades: int = 0
    expectancy_usdc: float = 0.0
    win_rate: float = 0.0
    trade_sharpe: float = 0.0
    profit_factor: float = 0.0
    avg_win_usdc: float = 0.0
    avg_loss_usdc: float = 0.0
    longest_losing_streak: int = 0
    execution_book_source: str = "unknown"
    execution_used_reconstructed_book: bool = False
    execution_snapshot_groups: int = 0
    execution_median_visible_notional_usdc: float = 0.0
    execution_depth_penalty: float = 1.0
    execution_prob_fill_on_limit: float = 0.5
    execution_prob_slippage: float = 0.5
    execution_warnings: list[str] | None = None


@dataclass
class BacktestRunResult:
    per_market: list[MarketBacktestResult]
    aggregate_pnl_usdc: float
    aggregate_n_fills: int
    aggregate_n_orders: int
    mean_sharpe: float
    aggregate_expectancy_usdc: float = 0.0
    aggregate_fill_rate: float = 0.0
    n_markets: int = 0
    n_markets_with_fills: int = 0
    max_longest_losing_streak: int = 0
    n_markets_using_reconstructed_books: int = 0
    aggregate_execution_warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregate_pnl_usdc": self.aggregate_pnl_usdc,
            "aggregate_n_fills": self.aggregate_n_fills,
            "aggregate_n_orders": self.aggregate_n_orders,
            "mean_sharpe": self.mean_sharpe,
            "aggregate_expectancy_usdc": self.aggregate_expectancy_usdc,
            "aggregate_fill_rate": self.aggregate_fill_rate,
            "n_markets": self.n_markets,
            "n_markets_with_fills": self.n_markets_with_fills,
            "max_longest_losing_streak": self.max_longest_losing_streak,
            "n_markets_using_reconstructed_books": self.n_markets_using_reconstructed_books,
            "aggregate_execution_warnings": self.aggregate_execution_warnings or [],
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
                    "n_pair_trades": r.n_pair_trades,
                    "expectancy_usdc": r.expectancy_usdc,
                    "win_rate": r.win_rate,
                    "trade_sharpe": r.trade_sharpe,
                    "profit_factor": r.profit_factor,
                    "avg_win_usdc": r.avg_win_usdc,
                    "avg_loss_usdc": r.avg_loss_usdc,
                    "longest_losing_streak": r.longest_losing_streak,
                    "execution_book_source": r.execution_book_source,
                    "execution_used_reconstructed_book": r.execution_used_reconstructed_book,
                    "execution_snapshot_groups": r.execution_snapshot_groups,
                    "execution_median_visible_notional_usdc": r.execution_median_visible_notional_usdc,
                    "execution_depth_penalty": r.execution_depth_penalty,
                    "execution_prob_fill_on_limit": r.execution_prob_fill_on_limit,
                    "execution_prob_slippage": r.execution_prob_slippage,
                    "execution_warnings": r.execution_warnings or [],
                }
                for r in self.per_market
            ],
        }


def _target_order_notional_usdc(strategy_params: dict[str, Any] | None) -> float:
    params = strategy_params or {}
    try:
        return max(1.0, float(params.get("order_notional_usdc", 5.0)))
    except Exception:
        return 5.0



def _execution_realism_from_orderbook_df(
    orderbook_df: pd.DataFrame,
    *,
    order_notional_usdc: float,
    book_source: str,
) -> dict[str, Any]:
    warnings: list[str] = []
    snapshot_groups = 0
    median_visible_notional_usdc = 0.0
    depth_penalty = 1.0
    prob_fill_on_limit = 0.5
    prob_slippage = 0.5
    used_reconstructed_book = book_source != "snapshots"

    if orderbook_df is not None and not orderbook_df.empty:
        snapshot_groups = int(orderbook_df["timestamp"].nunique()) if "timestamp" in orderbook_df.columns else 0
        visible_notionals: list[float] = []
        if {"timestamp", "side", "price", "size"}.issubset(orderbook_df.columns):
            grouped = orderbook_df.groupby(["timestamp", "side"], sort=True)
            for (_ts, side), group in grouped:
                side_df = group.sort_values("price", ascending=(str(side) == "ask"))
                top = side_df.iloc[0]
                visible_notionals.append(max(0.0, float(top["price"]) * float(top["size"])))
        if visible_notionals:
            visible_notionals.sort()
            median_visible_notional_usdc = float(visible_notionals[len(visible_notionals) // 2])

    if used_reconstructed_book:
        warnings.append("reconstructed_book_fallback")
        depth_penalty = min(depth_penalty, 0.5)
        prob_fill_on_limit = min(prob_fill_on_limit, 0.35)
        prob_slippage = max(prob_slippage, 0.7)

    if median_visible_notional_usdc > 0.0:
        depth_penalty = min(depth_penalty, min(1.0, median_visible_notional_usdc / max(order_notional_usdc, 1e-9)))
        if depth_penalty < 1.0:
            warnings.append("shallow_visible_depth")
            prob_fill_on_limit = min(prob_fill_on_limit, max(0.15, 0.5 * depth_penalty))
            prob_slippage = max(prob_slippage, min(0.9, 0.5 + 0.5 * (1.0 - depth_penalty)))

    return {
        "book_source": book_source,
        "used_reconstructed_book": used_reconstructed_book,
        "snapshot_groups": snapshot_groups,
        "median_visible_notional_usdc": round(median_visible_notional_usdc, 6),
        "depth_penalty": round(depth_penalty, 6),
        "prob_fill_on_limit": round(prob_fill_on_limit, 6),
        "prob_slippage": round(prob_slippage, 6),
        "warnings": warnings,
    }



def _load_execution_inputs(
    *,
    catalog,
    token_id: str,
    instrument,
    start: datetime,
    end: datetime,
    order_notional_usdc: float,
) -> tuple[list[Any], dict[str, Any]]:
    orderbook_df = catalog.read_orderbook_history(token_id, start, end)
    snapshot_deltas = load_book_as_order_book_deltas(catalog, token_id, instrument, start, end)
    if snapshot_deltas:
        realism = _execution_realism_from_orderbook_df(
            orderbook_df,
            order_notional_usdc=order_notional_usdc,
            book_source="snapshots",
        )
        return snapshot_deltas, realism

    reconstructed = reconstruct_book_from_trades(catalog, token_id, instrument, start, end)
    realism = _execution_realism_from_orderbook_df(
        pd.DataFrame(),
        order_notional_usdc=order_notional_usdc,
        book_source="reconstructed_trades",
    )
    return reconstructed, realism



def _combine_execution_realism(*parts: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    for part in parts:
        for warning in part.get("warnings", []):
            if warning not in warnings:
                warnings.append(warning)
    sources = {str(p.get("book_source", "unknown")) for p in parts}
    if len(sources) == 1:
        book_source = next(iter(sources))
    else:
        book_source = "mixed"
    return {
        "book_source": book_source,
        "used_reconstructed_book": any(bool(p.get("used_reconstructed_book")) for p in parts),
        "snapshot_groups": int(sum(int(p.get("snapshot_groups", 0)) for p in parts)),
        "median_visible_notional_usdc": min(float(p.get("median_visible_notional_usdc", 0.0)) for p in parts) if parts else 0.0,
        "depth_penalty": min(float(p.get("depth_penalty", 1.0)) for p in parts) if parts else 1.0,
        "prob_fill_on_limit": min(float(p.get("prob_fill_on_limit", 0.5)) for p in parts) if parts else 0.5,
        "prob_slippage": max(float(p.get("prob_slippage", 0.5)) for p in parts) if parts else 0.5,
        "warnings": warnings,
    }


class BacktestRunner:
    """NautilusTrader-backed backtesting runner for binary complement arbs."""

    def __init__(
        self,
        config: TradingConfig,
        data_dir: Path | None = None,
        strategy_module: str | None = None,
        strategy_class: str | None = None,
        strategy_config_class: str | None = None,
        strategy_params: dict[str, Any] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        strategy_module, strategy_class, strategy_config_class
            When set, dispatch to a hypothesis-provided strategy instead of
            the default `BinaryArbStrategy`. Used by `run_hypothesis()` which
            reads these from the hypothesis frontmatter.
        strategy_params
            Kwargs to pass to the strategy's *Config constructor.
        """
        self._config = config
        self._data_dir = data_dir or Path("data/parquet")
        self._strategy_module = strategy_module
        self._strategy_class = strategy_class
        self._strategy_config_class = strategy_config_class
        self._strategy_params = strategy_params or {}

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
        from trading_lab.data.market_catalog import MarketCatalog
        from trading_lab.data.market_filter import MarketCriteria, select_markets

        md_path = hypotheses_dir / f"{hypothesis_slug}.md"
        criteria_dict, _body = _parse_hypothesis(md_path)
        if criteria_dict is None:
            raise FileNotFoundError(f"hypothesis missing: {md_path}")
        criteria = MarketCriteria.from_dict(criteria_dict)

        catalog = MarketCatalog(market_catalog_path)
        markets = select_markets(criteria, catalog)
        catalog.close()
        log.info("hypothesis %s selected %d markets", hypothesis_slug, len(markets))

        # If the hypothesis specifies its own strategy, use it; otherwise
        # fall back to the BinaryArbStrategy default already configured.
        fm = _parse_hypothesis_frontmatter(md_path)
        if not self._strategy_module:
            self._strategy_module = fm.get("strategy_module") or None
        if not self._strategy_class:
            self._strategy_class = fm.get("strategy_class") or None
        if not self._strategy_config_class:
            self._strategy_config_class = fm.get("strategy_config_class") or None

        results: list[MarketBacktestResult] = []
        # NT's Rust logger panics on re-init in the same process. Each
        # per-market backtest runs in a subprocess so it gets a fresh logger.
        # In-process `_run_single` is fine as long as it's called at most
        # once per process (e.g. `run_pair` path).
        for m in markets:
            if not m.yes_token_id or not m.no_token_id:
                log.warning("skipping %s: missing yes/no token ids", m.condition_id)
                continue
            try:
                r = self._run_single_subprocess(
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

    def _run_single_subprocess(
        self,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        start: datetime,
        end: datetime,
        initial_capital_usdc: float,
        question: str,
    ) -> MarketBacktestResult:
        """Run one market backtest in a subprocess for logger isolation."""
        import json as _json
        import os
        import subprocess
        import sys

        env = os.environ.copy()
        # Pass strategy refs via env so the subprocess builds the same strategy.
        if self._strategy_module:
            env["NP_STRATEGY_MODULE"] = self._strategy_module
        if self._strategy_class:
            env["NP_STRATEGY_CLASS"] = self._strategy_class
        if self._strategy_config_class:
            env["NP_STRATEGY_CONFIG_CLASS"] = self._strategy_config_class
        if self._strategy_params:
            env["NP_STRATEGY_PARAMS_JSON"] = _json.dumps(self._strategy_params)

        cmd = [
            sys.executable, "scripts/backtest.py",
            "--condition-id", condition_id,
            "--yes-token-id", yes_token_id,
            "--no-token-id", no_token_id,
            "--start", start.date().isoformat(),
            "--end", end.date().isoformat(),
            "--initial-capital-usdc", str(initial_capital_usdc),
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(
                f"per-market backtest crashed (rc={proc.returncode}): "
                f"{proc.stderr[-300:]}"
            )
        line = None
        for raw in reversed(proc.stdout.strip().splitlines()):
            s = raw.strip()
            if s.startswith("{"):
                line = s
                break
        if not line:
            raise RuntimeError("no JSON in subprocess stdout")
        summary = _json.loads(line)
        pm = (summary.get("per_market") or [{}])[0]
        return MarketBacktestResult(
            condition_id=condition_id,
            question=question or pm.get("question", ""),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            n_trade_ticks=int(pm.get("n_trade_ticks", 0)),
            n_orders=int(pm.get("n_orders", 0)),
            n_fills=int(pm.get("n_fills", 0)),
            pnl_usdc=float(pm.get("pnl_usdc", 0.0)),
            sharpe=float(pm.get("sharpe", 0.0)),
            max_drawdown_pct=float(pm.get("max_drawdown_pct", 0.0)),
            fill_rate=float(pm.get("fill_rate", 0.0)),
            kill_switch_triggered=bool(pm.get("kill_switch_triggered", False)),
            n_pair_trades=int(pm.get("n_pair_trades", 0)),
            expectancy_usdc=float(pm.get("expectancy_usdc", 0.0)),
            win_rate=float(pm.get("win_rate", 0.0)),
            trade_sharpe=float(pm.get("trade_sharpe", 0.0)),
            profit_factor=float(pm.get("profit_factor", 0.0)),
            avg_win_usdc=float(pm.get("avg_win_usdc", 0.0)),
            avg_loss_usdc=float(pm.get("avg_loss_usdc", 0.0)),
            longest_losing_streak=int(pm.get("longest_losing_streak", 0)),
        )

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

        from trading_lab.data.catalog import DataCatalog

        log.info(
            "backtest start condition=%s yes=%s.. no=%s..",
            condition_id,
            yes_token_id[:14],
            no_token_id[:14],
        )

        data_catalog = DataCatalog(self._data_dir)
        yes_instr = make_instrument(yes_token_id, condition_id, question=question)
        no_instr = make_instrument(no_token_id, condition_id, question=question)
        order_notional_usdc = _target_order_notional_usdc(self._strategy_params)

        yes_ticks = load_trades_as_trade_ticks(data_catalog, yes_token_id, yes_instr, start, end)
        no_ticks = load_trades_as_trade_ticks(data_catalog, no_token_id, no_instr, start, end)
        yes_deltas, yes_realism = _load_execution_inputs(
            catalog=data_catalog,
            token_id=yes_token_id,
            instrument=yes_instr,
            start=start,
            end=end,
            order_notional_usdc=order_notional_usdc,
        )
        no_deltas, no_realism = _load_execution_inputs(
            catalog=data_catalog,
            token_id=no_token_id,
            instrument=no_instr,
            start=start,
            end=end,
            order_notional_usdc=order_notional_usdc,
        )
        combined_realism = _combine_execution_realism(yes_realism, no_realism)

        log.info(
            "data loaded yes_ticks=%d no_ticks=%d yes_deltas=%d no_deltas=%d source=%s depth=$%.2f warnings=%s",
            len(yes_ticks),
            len(no_ticks),
            len(yes_deltas),
            len(no_deltas),
            combined_realism["book_source"],
            combined_realism["median_visible_notional_usdc"],
            combined_realism["warnings"],
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
                n_pair_trades=0,
                expectancy_usdc=0.0,
                win_rate=0.0,
                trade_sharpe=0.0,
                profit_factor=0.0,
                avg_win_usdc=0.0,
                avg_loss_usdc=0.0,
                longest_losing_streak=0,
                execution_book_source=combined_realism["book_source"],
                execution_used_reconstructed_book=bool(combined_realism["used_reconstructed_book"]),
                execution_snapshot_groups=int(combined_realism["snapshot_groups"]),
                execution_median_visible_notional_usdc=float(combined_realism["median_visible_notional_usdc"]),
                execution_depth_penalty=float(combined_realism["depth_penalty"]),
                execution_prob_fill_on_limit=float(combined_realism["prob_fill_on_limit"]),
                execution_prob_slippage=float(combined_realism["prob_slippage"]),
                execution_warnings=list(combined_realism["warnings"]),
            )

        # NautilusTrader's Rust logger is a process-global singleton; passing
        # a `LoggingConfig` on a second engine in the same process panics
        # ("attempted to set a logger after the logging system was already
        # initialized"). Only configure logging on the first engine; later
        # engines reuse the existing logger.
        global _NT_LOGGER_INITIALISED
        if not _NT_LOGGER_INITIALISED:
            engine = BacktestEngine(
                config=BacktestEngineConfig(
                    trader_id=TraderId("BACKTEST-001"),
                    logging=LoggingConfig(log_level="WARN"),
                )
            )
            _NT_LOGGER_INITIALISED = True
        else:
            engine = BacktestEngine(
                config=BacktestEngineConfig(trader_id=TraderId("BACKTEST-001"))
            )

        engine.add_venue(
            venue=Venue("POLYMARKET"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(initial_capital_usdc, USDC)],
            fill_model=self._build_fill_model(combined_realism),
            latency_model=self._build_latency_model(),
            book_type=BookType.L2_MBP,
            trade_execution=True,
        )

        engine.add_instrument(yes_instr)
        engine.add_instrument(no_instr)

        # Merge ticks + deltas and let engine sort. Engine handles ordering.
        engine.add_data(yes_ticks + no_ticks, sort=True)
        engine.add_data(yes_deltas + no_deltas, sort=True)

        strategy = self._build_strategy(condition_id, yes_instr, no_instr)
        engine.add_strategy(strategy)

        engine.run()

        result = self._extract_result(
            engine,
            condition_id,
            question,
            yes_token_id,
            no_token_id,
            len(yes_ticks) + len(no_ticks),
            execution_realism=combined_realism,
        )
        engine.dispose()
        return result

    def _build_strategy(self, condition_id: str, yes_instr, no_instr):
        """
        Construct the strategy instance for this single-market run.

        If a `strategy_module/strategy_class` was passed (from the hypothesis
        frontmatter), import and instantiate it. Otherwise fall back to the
        default `BinaryArbStrategy`. Pre-registers a single pair via
        `register_market_pair` if the strategy supports it, else assigns
        `_initial_pair` for strategies that consume it in `on_start`.

        The strategy's *Config is instantiated with `self._strategy_params`
        kwargs filtered to fields the config actually exposes.
        """
        if self._strategy_module and self._strategy_class:
            import importlib

            mod = importlib.import_module(self._strategy_module)
            cls = getattr(mod, self._strategy_class)
            cfg_cls = (
                getattr(mod, self._strategy_config_class)
                if self._strategy_config_class
                else None
            )
            cfg = (
                cfg_cls(**_filter_to_fields(cfg_cls, self._strategy_params))
                if cfg_cls
                else None
            )
            strategy = cls(config=cfg) if cfg is not None else cls()
        else:
            from trading_lab.strategies.arb_complement import (
                BinaryArbConfig,
                BinaryArbStrategy,
            )

            cfg = BinaryArbConfig(
                strategy_id=f"ARB-{condition_id[:8]}",
                min_profit_usdc=self._config.arb.min_profit_usdc,
                max_capital_usdc=self._config.arb.max_capital_usdc,
            )
            strategy = BinaryArbStrategy(config=cfg)

        # Pair registration — try known idioms in order of preference.
        if hasattr(strategy, "register_market_pair"):
            strategy.register_market_pair(condition_id, yes_instr.id, no_instr.id)
        elif hasattr(strategy, "register_instrument"):
            strategy.register_instrument(yes_instr.id)
            strategy.register_instrument(no_instr.id)
        else:
            strategy._initial_pair = (condition_id, yes_instr.id, no_instr.id)  # type: ignore[attr-defined]
        return strategy

    def _build_fill_model(self, execution_realism: dict[str, Any] | None = None):
        from nautilus_trader.backtest.models import FillModel

        realism = execution_realism or {}
        # Snapshot-first execution inputs keep the old pessimistic defaults.
        # Reconstructed-book fallback and shallow visible depth lower fill odds
        # and raise slippage further.
        return FillModel(
            prob_fill_on_limit=float(realism.get("prob_fill_on_limit", 0.5)),
            prob_slippage=float(realism.get("prob_slippage", 0.5)),
        )

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
        *,
        execution_realism: dict[str, Any] | None = None,
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

        # Per-pair PnL series — pairs entries with closes FIFO per
        # instrument, treats matched YES+NO pairs at $1.00 at resolution
        # for arb-style strategies. The per-pair Sharpe is meaningful
        # (mean / stdev / sqrt(N)); the cash-equity Sharpe is not (it dips
        # monotonically as capital is deployed and never recovers in
        # hold-to-resolution strategies).
        per_pair_pnls = self._per_pair_pnl_series(orders_df)
        trade_metrics = self._trade_metrics(per_pair_pnls)
        longest_losing_streak = _longest_losing_streak(per_pair_pnls)
        if per_pair_pnls:
            import math
            mean = sum(per_pair_pnls) / len(per_pair_pnls)
            if len(per_pair_pnls) > 1:
                var = sum((p - mean) ** 2 for p in per_pair_pnls) / (len(per_pair_pnls) - 1)
                stdev = math.sqrt(var)
            else:
                stdev = 0.0
            sharpe = (mean / stdev * math.sqrt(len(per_pair_pnls))) if stdev > 0 else 0.0
            pnl = sum(per_pair_pnls)
            # Max DD from the running cumulative pair-PnL curve.
            equity = []
            cum = 0.0
            for p in per_pair_pnls:
                cum += p
                equity.append(cum)
            peak = equity[0]
            max_dd_abs = 0.0
            for v in equity:
                peak = max(peak, v)
                dd = (v - peak)
                if dd < max_dd_abs:
                    max_dd_abs = dd
            initial_capital = 10_000.0  # for percentage normalisation
            max_dd_pct = (max_dd_abs / initial_capital) * 100.0
        else:
            # Fallback to legacy cash-equity sharpe + terminal PnL for
            # strategies where pair-pairing doesn't make sense (no fills).
            sharpe, max_dd_pct = self._equity_metrics(account_df)
            pnl = self._terminal_pnl(engine, orders_df, venue)
        realism = execution_realism or {}
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
            n_pair_trades=int(trade_metrics.get("n_trades", 0)),
            expectancy_usdc=float(trade_metrics.get("expectancy", 0.0)),
            win_rate=float(trade_metrics.get("win_rate", 0.0)),
            trade_sharpe=float(trade_metrics.get("trade_sharpe", 0.0)),
            profit_factor=float(trade_metrics.get("profit_factor", 0.0)),
            avg_win_usdc=float(trade_metrics.get("avg_win", 0.0)),
            avg_loss_usdc=float(trade_metrics.get("avg_loss", 0.0)),
            longest_losing_streak=int(longest_losing_streak),
            execution_book_source=str(realism.get("book_source", "unknown")),
            execution_used_reconstructed_book=bool(realism.get("used_reconstructed_book", False)),
            execution_snapshot_groups=int(realism.get("snapshot_groups", 0)),
            execution_median_visible_notional_usdc=float(realism.get("median_visible_notional_usdc", 0.0)),
            execution_depth_penalty=float(realism.get("depth_penalty", 1.0)),
            execution_prob_fill_on_limit=float(realism.get("prob_fill_on_limit", 0.5)),
            execution_prob_slippage=float(realism.get("prob_slippage", 0.5)),
            execution_warnings=list(realism.get("warnings", [])),
        )

    def _trade_metrics(self, per_pair_pnls: list[float]) -> dict[str, float]:
        from trading_lab.research.metrics import compute_trade_metrics

        return compute_trade_metrics(per_pair_pnls)

    def _per_pair_pnl_series(self, orders_df: pd.DataFrame) -> list[float]:
        """
        FIFO-pair fills per instrument to produce a realised PnL series.

        For a binary-arb backtest, each instrument is traded BUY-only and
        held to resolution — there are no opposite-side closes mid-window.
        We treat each fill as one "pair-half"; the realised pair-PnL is
        `(1.0 - avg_fill_price) * filled_qty`, i.e. the genuine arb edge.

        For mean-revert / two-sided strategies, this still computes a
        meaningful series: each opposite-side close fills the previous
        entry FIFO; the realised PnL is `(close_px - entry_px) * qty`
        for longs (inverted for shorts).
        """
        if orders_df is None or orders_df.empty:
            return []
        try:
            df = orders_df.copy()
            df["filled_qty"] = pd.to_numeric(df.get("filled_qty", 0), errors="coerce").fillna(0)
            df["avg_px"] = pd.to_numeric(df.get("avg_px", 0), errors="coerce").fillna(0)
            df = df[df["filled_qty"] > 0]
            if df.empty:
                return []
        except Exception:
            return []

        # Order rows by event time so FIFO is honoured.
        ts_col = None
        for cand in ("ts_init", "ts_event", "ts_last", "timestamp"):
            if cand in df.columns:
                ts_col = cand
                break
        if ts_col is not None:
            df = df.sort_values(ts_col)

        # Distinct instruments — if there are exactly 2, we infer
        # arb-style pairing: each matched YES + NO pair pays $1.00 at
        # resolution → realised pair-PnL = (1 - yes_px - no_px) * qty.
        # This treats the arb as a SINGLE position (matched share-pair),
        # not as two independent winners. FIFO across the two legs.
        instruments = df["instrument_id"].unique() if "instrument_id" in df.columns else []
        if len(instruments) == 2:
            from collections import deque as _deque

            yes_iid, no_iid = instruments[0], instruments[1]
            yes_q: _deque = _deque()
            no_q: _deque = _deque()
            pnls_arb: list[float] = []
            for row in df.itertuples(index=False):
                px = float(row.avg_px)
                qty = float(row.filled_qty)
                if str(row.instrument_id) == str(yes_iid):
                    queue, other = yes_q, no_q
                else:
                    queue, other = no_q, yes_q
                # If the other leg has open shares, pair them off.
                while qty > 0 and other:
                    other_leg = other[0]
                    match = min(qty, other_leg["qty"])
                    if str(row.instrument_id) == str(yes_iid):
                        pnls_arb.append(
                            (1.0 - px - other_leg["px"]) * match
                        )
                    else:
                        pnls_arb.append(
                            (1.0 - other_leg["px"] - px) * match
                        )
                    qty -= match
                    other_leg["qty"] -= match
                    if other_leg["qty"] <= 0:
                        other.popleft()
                if qty > 0:
                    queue.append({"px": px, "qty": qty})
            return pnls_arb

        # General path: per-instrument FIFO entry/close pairing.
        from collections import defaultdict, deque

        open_by_iid: dict[str, deque] = defaultdict(deque)
        pnls: list[float] = []
        for row in df.itertuples(index=False):
            iid = str(row.instrument_id)
            side = str(getattr(row, "order_side", "")).upper()
            px = float(row.avg_px)
            qty = float(row.filled_qty)
            q = open_by_iid[iid]
            # Match against first opposite-side open fill.
            matched_idx = None
            for i, opener in enumerate(q):
                if str(opener["side"]) != side:
                    matched_idx = i
                    break
            if matched_idx is not None:
                opener = q[matched_idx]
                match_qty = min(qty, opener["qty"])
                if opener["side"] == "BUY":
                    pnls.append((px - opener["px"]) * match_qty)
                else:
                    pnls.append((opener["px"] - px) * match_qty)
                del q[matched_idx]
            else:
                q.append({"side": side, "px": px, "qty": qty})
        return pnls

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


def _filter_to_fields(cfg_cls, params: dict[str, Any]) -> dict[str, Any]:
    """Keep only kwargs that the StrategyConfig class declares.

    NautilusTrader's StrategyConfig is a `msgspec.Struct`, not a Pydantic
    model — fields live in `__struct_fields__`. Fallback to pydantic
    `model_fields` for the off-chance of a non-NT config class.
    """
    allowed: set[str] = set()
    if hasattr(cfg_cls, "__struct_fields__"):
        allowed = set(cfg_cls.__struct_fields__)
    elif hasattr(cfg_cls, "model_fields"):
        allowed = set(cfg_cls.model_fields.keys())
    if not allowed:
        return params
    return {k: v for k, v in params.items() if k in allowed}


def _parse_hypothesis_frontmatter(path: Path) -> dict[str, Any]:
    """Read just the frontmatter as a dict (returns {} on any failure)."""
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        import yaml

        return yaml.safe_load(text[3:end].strip()) or {}
    except Exception:
        return {}


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
        return BacktestRunResult(
            per_market=[],
            aggregate_pnl_usdc=0.0,
            aggregate_n_fills=0,
            aggregate_n_orders=0,
            mean_sharpe=0.0,
            aggregate_expectancy_usdc=0.0,
            aggregate_fill_rate=0.0,
            n_markets=0,
            n_markets_with_fills=0,
            max_longest_losing_streak=0,
            n_markets_using_reconstructed_books=0,
            aggregate_execution_warnings=[],
        )
    total_pnl = sum(r.pnl_usdc for r in results)
    total_fills = sum(r.n_fills for r in results)
    total_orders = sum(r.n_orders for r in results)
    total_pair_trades = sum(r.n_pair_trades for r in results)
    mean_sharpe = sum(r.sharpe for r in results) / len(results)
    n_markets_with_fills = sum(1 for r in results if r.n_fills > 0)
    aggregate_execution_warnings: list[str] = []
    for result in results:
        for warning in result.execution_warnings or []:
            if warning not in aggregate_execution_warnings:
                aggregate_execution_warnings.append(warning)
    return BacktestRunResult(
        per_market=results,
        aggregate_pnl_usdc=total_pnl,
        aggregate_n_fills=total_fills,
        aggregate_n_orders=total_orders,
        mean_sharpe=mean_sharpe,
        aggregate_expectancy_usdc=(total_pnl / total_pair_trades) if total_pair_trades else 0.0,
        aggregate_fill_rate=(total_fills / total_orders) if total_orders else 0.0,
        n_markets=len(results),
        n_markets_with_fills=n_markets_with_fills,
        max_longest_losing_streak=max((r.longest_losing_streak for r in results), default=0),
        n_markets_using_reconstructed_books=sum(1 for r in results if r.execution_used_reconstructed_book),
        aggregate_execution_warnings=aggregate_execution_warnings,
    )


def _longest_losing_streak(per_trade_pnls: list[float]) -> int:
    longest = 0
    current = 0
    for pnl in per_trade_pnls:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _coerce_dt(d: datetime | str) -> datetime:
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return datetime.fromisoformat(d).replace(tzinfo=UTC)
