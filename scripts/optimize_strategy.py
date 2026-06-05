#!/usr/bin/env python3
"""
Drain the OPTIMIZE queue.

For one hypothesis:
  1. Read its `Parameter Space` from the MD body (a `# Parameter space`
     section listing `name: [v1, v2, v3]` pairs — see arb-complement.md).
  2. Run the cartesian product backtest grid over `--train-start/--train-end`.
  3. For the top-K (default 3) by PnL, run walk-forward: split the
     [data-start, data-end] window into N non-overlapping segments. One MUST
     include the last 30 days of available data (the "recent regime" gate).
  4. Apply decision rules and transition the hypothesis.

Decision rules (Phase 5.6 simplification):
  oos_mean_sharpe < 0.7              → REJECTED (overfit)
  recent_oos_sharpe < 0.7            → SHELVED  (regime_change)
  oos_mean_sharpe < 1.0              → SHELVED  (marginal_oos)
  oos_mean_sharpe >= 1.0 AND
      oos_mean_sharpe >= 0.6 * is_sharpe → PAPER_READY

Adapted-for-arb override (same intent as eval_strategy.py): if PnL > 0 in
both train and EVERY OOS window AND total n_trades >= 100, promote to
PAPER_READY even when cash-equity Sharpe is the artefactual negative we
get from hold-to-resolution strategies.

Records every grid + WF run as an `experiments` row. Prints JSON on stdout.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from trading_lab.research.optimizer_artifacts import (
    default_output_path,
    write_optimizer_artifact,
)

log = logging.getLogger("optimize")


@dataclass
class BacktestMetrics:
    pnl: float
    sharpe: float
    max_dd: float
    n_trades: int
    expectancy_usdc: float
    fill_rate: float
    n_orders: int
    n_fills: int
    n_markets: int
    n_markets_with_fills: int
    longest_losing_streak: int


@dataclass
class GridResult:
    params: dict[str, float]
    metrics: BacktestMetrics
    methodology: dict
    methodology_state: str
    methodology_category: str
    methodology_score: float


@dataclass
class WalkForwardResult:
    params: dict[str, float]
    oos_windows: list[dict[str, float]]
    oos_mean_sharpe: float
    oos_mean_pnl: float
    recent_oos_sharpe: float
    recent_oos_pnl: float
    is_sharpe: float
    methodology: dict
    methodology_state: str
    methodology_category: str
    methodology_score: float


# ---------------------------------------------------------------------------
# Hypothesis MD parsing
# ---------------------------------------------------------------------------


def parse_param_space(md_path: Path) -> dict[str, list[float]]:
    """
    Find the `## Parameter space` section and parse `name: [v1, v2]` lines.

    Accepts list-of-numbers values only — anything else is skipped with a
    warning (keeps the agent from injecting code via this path).
    """
    if not md_path.exists():
        return {}
    text = md_path.read_text()
    body_start = text.find("\n---\n", 3)
    body = text[body_start + 5 :] if body_start >= 0 else text

    # Find the parameter-space section
    m = re.search(r"#+\s*Parameter\s+space\s*\n(.+?)(?=\n#+\s|\Z)", body, re.S | re.I)
    if not m:
        return {}
    section = m.group(1)

    space: dict[str, list[float]] = {}
    for line in section.splitlines():
        line = line.strip().lstrip("-").lstrip("*").strip()
        if not line:
            continue
        # Match "name: [v1, v2, v3]" possibly with backticks around name
        bm = re.match(r"`?([a-zA-Z_][a-zA-Z0-9_]*)`?\s*:\s*\[(.+?)\]", line)
        if not bm:
            continue
        name, values_raw = bm.group(1), bm.group(2)
        try:
            # Preserve int-typed values so strategies that take ints
            # (e.g., `deque(maxlen=N)` requires int) don't crash on a coerced
            # float. Any value without a `.` is treated as int; everything
            # else floats.
            values: list[float | int] = []
            for v in values_raw.split(","):
                s = v.strip()
                if "." in s or "e" in s.lower():
                    values.append(float(s))
                else:
                    values.append(int(s))
        except ValueError:
            log.warning("skipping unparseable values for %s: %s", name, values_raw)
            continue
        space[name] = values  # type: ignore[assignment]
    return space


# ---------------------------------------------------------------------------
# Backtest invocation (single config)
# ---------------------------------------------------------------------------


def run_single_backtest(
    hypothesis_slug: str,
    params: dict[str, float],
    start: datetime,
    end: datetime,
    initial_capital_usdc: float,
) -> BacktestMetrics:
    """
    Run one backtest as a subprocess and parse its --json output.

    Subprocess (rather than calling BacktestRunner in-process) is required
    because NautilusTrader's logger is a global that panics on re-init when
    a second engine instance is created in the same process.
    """
    env = os.environ.copy()
    # Legacy: BinaryArbStrategy reads min/max from TradingConfig.arb via env.
    if "min_profit_usdc" in params:
        env["ARB_MIN_PROFIT_USDC"] = str(params["min_profit_usdc"])
    if "max_capital_usdc" in params:
        env["ARB_MAX_CAPITAL_USDC"] = str(params["max_capital_usdc"])
    # General path: pass the full param dict as JSON so agent-written
    # strategies whose configs aren't bound to ARB_* env vars also get the
    # right values. BacktestRunner reads NP_STRATEGY_PARAMS_JSON and forwards
    # them as kwargs to the strategy's *Config constructor.
    env["NP_STRATEGY_PARAMS_JSON"] = json.dumps(params)

    cmd = [
        sys.executable, "scripts/backtest.py",
        "--hypothesis-slug", hypothesis_slug,
        "--start", start.date().isoformat(),
        "--end", end.date().isoformat(),
        "--initial-capital-usdc", str(initial_capital_usdc),
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"backtest subprocess failed (rc={proc.returncode}): "
            f"stderr={proc.stderr[-500:]}"
        )
    # The script prints a single JSON line at the end.
    last_json_line = None
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            last_json_line = line
            break
    if last_json_line is None:
        raise RuntimeError(f"no JSON in backtest stdout: {proc.stdout[-500:]}")
    summary = json.loads(last_json_line)
    pnl = float(summary["aggregate_pnl_usdc"])
    sharpe = float(summary["mean_sharpe"])
    n_trades = int(summary["aggregate_n_fills"])
    max_dd = min(
        (m["max_drawdown_pct"] for m in summary.get("per_market", [])),
        default=0.0,
    )
    return BacktestMetrics(
        pnl=pnl,
        sharpe=sharpe,
        max_dd=max_dd,
        n_trades=n_trades,
        expectancy_usdc=float(summary.get("aggregate_expectancy_usdc", 0.0)),
        fill_rate=float(summary.get("aggregate_fill_rate", 0.0)),
        n_orders=int(summary.get("aggregate_n_orders", 0)),
        n_fills=int(summary.get("aggregate_n_fills", 0)),
        n_markets=int(summary.get("n_markets", len(summary.get("per_market", [])))),
        n_markets_with_fills=int(summary.get("n_markets_with_fills", 0)),
        longest_losing_streak=int(summary.get("max_longest_losing_streak", 0)),
    )


def _methodology_for_metrics(metrics: BacktestMetrics, *, min_trades: int, min_fill_rate: float, min_markets_with_fills: int) -> tuple[dict, str, str, float]:
    from trading_lab.agent.lifecycle import State
    from trading_lab.research.eval_methodology import assess_backtest
    from trading_lab.research.experiment_reporting import score_backtest_result

    decision = assess_backtest(
        state_enum=State,
        sharpe=metrics.sharpe,
        max_dd_pct=metrics.max_dd,
        n_trades=metrics.n_trades,
        pnl_usdc=metrics.pnl,
        expectancy_usdc=metrics.expectancy_usdc,
        fill_rate=metrics.fill_rate,
        n_orders=metrics.n_orders,
        n_fills=metrics.n_fills,
        n_markets=metrics.n_markets,
        n_markets_with_fills=metrics.n_markets_with_fills,
        min_trades=min_trades,
        min_fill_rate=min_fill_rate,
        min_markets_with_fills=min_markets_with_fills,
    )
    enriched = {
        "pnl": metrics.pnl,
        "sharpe": metrics.sharpe,
        "max_dd": metrics.max_dd,
        "n_trades": metrics.n_trades,
        "expectancy_usdc": metrics.expectancy_usdc,
        "fill_rate": metrics.fill_rate,
        "n_orders": metrics.n_orders,
        "n_fills": metrics.n_fills,
        "n_markets": metrics.n_markets,
        "n_markets_with_fills": metrics.n_markets_with_fills,
        "methodology": decision.methodology,
        "methodology_decision_state": decision.new_state,
    }
    return decision.methodology, decision.new_state, decision.rejection_category, score_backtest_result(enriched)


# ---------------------------------------------------------------------------
# Decision rules
# ---------------------------------------------------------------------------


def decide_oos(wf: WalkForwardResult) -> tuple[str, str]:
    from trading_lab.agent.lifecycle import State

    # Only count windows that produced trades — zero-trade windows are
    # data-coverage artefacts, not strategy failures.
    active_windows = [w for w in wf.oos_windows if int(w.get("n_trades", 0)) > 0]
    pnl_positive_all = (
        wf.recent_oos_pnl > 0
        and len(active_windows) >= 1
        and all(w["pnl"] > 0 for w in active_windows)
    )
    total_trades = sum(int(w.get("n_trades", 0)) for w in active_windows)

    if wf.oos_mean_sharpe < 0.7 and not pnl_positive_all:
        return State.REJECTED.value, "overfit"
    if wf.recent_oos_sharpe < 0.7 and not (wf.recent_oos_pnl > 0):
        return State.SHELVED.value, "regime_change"
    if wf.oos_mean_sharpe < 1.0 and not (pnl_positive_all and total_trades >= 100):
        return State.SHELVED.value, "marginal_oos"

    # Heuristic IS-degradation gate: OOS Sharpe must be ≥ 0.6 × IS Sharpe.
    # Skip the gate when IS Sharpe is negative (the cash-equity artefact) and
    # PnL says we're winning anyway.
    if wf.is_sharpe > 0 and wf.oos_mean_sharpe < 0.6 * wf.is_sharpe:
        return State.SHELVED.value, "is_oos_gap"

    if pnl_positive_all and total_trades >= 100:
        return State.PAPER_READY.value, ""
    if wf.oos_mean_sharpe >= 1.0:
        return State.PAPER_READY.value, ""

    # Catch-all: shelf.
    return State.SHELVED.value, "marginal_oos"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--data-start", required=True, help="Full window start (YYYY-MM-DD)")
    p.add_argument("--data-end", required=True, help="Full window end (YYYY-MM-DD)")
    p.add_argument("--n-windows", type=int, default=3, help="Walk-forward window count")
    p.add_argument(
        "--top-k", type=int, default=3,
        help="How many top-by-PnL grid points to take to walk-forward",
    )
    p.add_argument(
        "--initial-capital-usdc", type=float, default=10_000.0,
    )
    p.add_argument(
        "--actor", default="agent:optimize",
    )
    p.add_argument(
        "--min-trades-floor", type=int, default=30,
        help="Drop grid points below this trade count before walk-forward",
    )
    p.add_argument(
        "--workers", type=int, default=4,
        help="Parallel grid+WF backtest workers (default 4; 1 = serial)",
    )
    p.add_argument(
        "--min-fill-rate", type=float, default=0.05,
        help="Methodology floor for acceptable fill rate when orders were placed.",
    )
    p.add_argument(
        "--min-markets-with-fills", type=int, default=2,
        help="Methodology floor for breadth when evaluating multi-market runs.",
    )
    p.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Optional explicit path for the JSON summary artifact",
    )
    p.add_argument(
        "--hypotheses-dir", type=Path, default=Path("research/hypotheses"),
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    p.add_argument(
        "--no-transition", action="store_true",
        help="Don't call lifecycle.transition()",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    from trading_lab.agent import budget, lifecycle

    # Budget guard
    ok, n, cap = budget.check("backtests", db_path=args.db)
    if not ok:
        print(json.dumps({"ok": False, "error": "budget_exhausted",
                          "consumed": n, "cap": cap}))
        return 3

    h = lifecycle.get_hypothesis(args.slug, db_path=args.db)
    if not h:
        print(json.dumps({"ok": False, "error": "hypothesis_not_found"}))
        return 2

    md_path = args.hypotheses_dir / f"{args.slug}.md"
    space = parse_param_space(md_path)
    if not space:
        print(json.dumps({
            "ok": False, "error": "no_parameter_space",
            "hint": f"Add '## Parameter space' section to {md_path}",
        }))
        return 2

    # Compute window split
    data_start = datetime.fromisoformat(args.data_start).replace(tzinfo=UTC)
    data_end = datetime.fromisoformat(args.data_end).replace(tzinfo=UTC)
    train_end = data_start + (data_end - data_start) * 0.7
    train_end = train_end.replace(microsecond=0)

    log.info(
        "grid: slug=%s params=%s train=[%s..%s]",
        args.slug, list(space.keys()),
        data_start.date(), train_end.date(),
    )

    # 1) Grid sweep over training window — parallelise across workers.
    names = list(space.keys())
    combos = [
        dict(zip(names, combo, strict=False))
        for combo in itertools.product(*(space[n] for n in names))
    ]

    def _run_grid_point(params):
        try:
            metrics = run_single_backtest(
                args.slug, params, data_start, train_end, args.initial_capital_usdc,
            )
            return params, metrics, None
        except Exception as exc:
            return params, None, str(exc)

    grid: list[GridResult] = []
    workers = max(1, args.workers)
    if workers == 1:
        results_iter = (_run_grid_point(p) for p in combos)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_run_grid_point, p) for p in combos]
            results_iter = (f.result() for f in as_completed(futs))

    for params, metrics, err in results_iter:
        if err is not None:
            log.warning("grid backtest failed params=%s err=%s", params, err)
            continue
        assert metrics is not None
        methodology, methodology_state, methodology_category, methodology_score = _methodology_for_metrics(
            metrics,
            min_trades=args.min_trades_floor,
            min_fill_rate=args.min_fill_rate,
            min_markets_with_fills=args.min_markets_with_fills,
        )
        budget.consume("backtests", db_path=args.db)
        lifecycle.record_experiment(
            slug=args.slug,
            params={
                **params,
                "_meta": {
                    "expectancy_usdc": metrics.expectancy_usdc,
                    "n_orders": metrics.n_orders,
                    "n_fills": metrics.n_fills,
                    "n_markets": metrics.n_markets,
                    "n_markets_with_fills": metrics.n_markets_with_fills,
                    "longest_losing_streak": metrics.longest_losing_streak,
                    "methodology_state": methodology_state,
                    "methodology_category": methodology_category,
                    "methodology_score": methodology_score,
                },
            },
            data_start=str(data_start.date()),
            data_end=str(train_end.date()),
            sharpe=metrics.sharpe,
            max_dd=metrics.max_dd,
            fill_rate=metrics.fill_rate,
            pnl=metrics.pnl,
            n_trades=metrics.n_trades,
            db_path=args.db,
        )
        grid.append(GridResult(
            params=params,
            metrics=metrics,
            methodology=methodology,
            methodology_state=methodology_state,
            methodology_category=methodology_category,
            methodology_score=methodology_score,
        ))
        log.info(
            "  params=%s pnl=$%.2f exp=%.4f fill=%.3f trades=%d score=%.1f",
            params,
            metrics.pnl,
            metrics.expectancy_usdc,
            metrics.fill_rate,
            metrics.n_trades,
            methodology_score,
        )

    if not grid:
        print(json.dumps({"ok": False, "error": "grid_empty"}))
        return 4

    # 2) Pick top-K by methodology-aware score (with trade-count floor)
    candidates = sorted(
        [g for g in grid if g.metrics.n_trades >= args.min_trades_floor],
        key=lambda g: (
            g.methodology_score,
            g.metrics.pnl,
            g.metrics.expectancy_usdc,
            g.metrics.sharpe,
            -abs(g.metrics.max_dd),
        ),
        reverse=True,
    )[: args.top_k]
    if not candidates:
        # No candidate cleared trade-count floor → REJECTED insufficient_trades.
        if not args.no_transition:
            try:
                lifecycle.transition(
                    args.slug, lifecycle.State.REJECTED.value,
                    "optimize: no grid point cleared 30-trade floor",
                    actor=args.actor,
                    rejection_category="insufficient_trades",
                    db_path=args.db,
                )
            except Exception as exc:
                log.warning("transition failed: %s", exc)
        print(json.dumps({"ok": True, "decision": "REJECTED",
                          "reason": "no_candidates_above_trade_floor",
                          "grid": [g.__dict__ for g in grid]}))
        return 0

    # 3) Walk-forward for each candidate
    full_span = data_end - data_start
    window_len = full_span / args.n_windows
    windows = [
        (data_start + i * window_len, data_start + (i + 1) * window_len)
        for i in range(args.n_windows)
    ]
    # Recent-regime: the window that includes (data_end - 30 days). If none
    # of the splits naturally hit it, force the last window to be the last 30d.
    last30_start = data_end - timedelta(days=30)
    if all(w[1] < last30_start for w in windows):
        windows[-1] = (last30_start, data_end)

    # Walk-forward jobs are independent — fan out across the same worker
    # pool. Each job = (candidate, window).
    wf_jobs = [
        (cand, w_start, w_end)
        for cand in candidates
        for (w_start, w_end) in windows
    ]

    def _run_wf_job(job):
        cand, w_start, w_end = job
        try:
            metrics = run_single_backtest(
                args.slug, cand.params, w_start, w_end,
                args.initial_capital_usdc,
            )
            return (cand, w_start, w_end, metrics, None)
        except Exception as exc:
            return (cand, w_start, w_end, None, str(exc))

    if workers == 1:
        wf_iter = (_run_wf_job(j) for j in wf_jobs)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_run_wf_job, j) for j in wf_jobs]
            wf_iter = (f.result() for f in as_completed(futs))

    # Bucket WF results by candidate so we can construct WalkForwardResult.
    per_cand: dict[int, list[dict]] = {id(c): [] for c in candidates}
    for cand, w_start, w_end, metrics, err in wf_iter:
        if err is not None:
            log.warning("WF backtest failed %s %s..%s: %s",
                        cand.params, w_start, w_end, err)
            continue
        assert metrics is not None
        methodology, methodology_state, methodology_category, methodology_score = _methodology_for_metrics(
            metrics,
            min_trades=args.min_trades_floor,
            min_fill_rate=args.min_fill_rate,
            min_markets_with_fills=args.min_markets_with_fills,
        )
        budget.consume("backtests", db_path=args.db)
        lifecycle.record_experiment(
            slug=args.slug,
            params={
                **cand.params,
                "_wf_window": f"{w_start.date()}..{w_end.date()}",
                "_meta": {
                    "expectancy_usdc": metrics.expectancy_usdc,
                    "n_orders": metrics.n_orders,
                    "n_fills": metrics.n_fills,
                    "n_markets": metrics.n_markets,
                    "n_markets_with_fills": metrics.n_markets_with_fills,
                    "longest_losing_streak": metrics.longest_losing_streak,
                    "methodology_state": methodology_state,
                    "methodology_category": methodology_category,
                    "methodology_score": methodology_score,
                },
            },
            data_start=str(w_start.date()),
            data_end=str(w_end.date()),
            sharpe=metrics.sharpe,
            max_dd=metrics.max_dd,
            fill_rate=metrics.fill_rate,
            pnl=metrics.pnl,
            n_trades=metrics.n_trades,
            db_path=args.db,
        )
        per_cand[id(cand)].append({
            "start": str(w_start.date()), "end": str(w_end.date()),
            "pnl": metrics.pnl, "sharpe": metrics.sharpe, "max_dd": metrics.max_dd,
            "n_trades": metrics.n_trades,
            "expectancy_usdc": metrics.expectancy_usdc,
            "fill_rate": metrics.fill_rate,
            "n_orders": metrics.n_orders,
            "n_fills": metrics.n_fills,
            "n_markets": metrics.n_markets,
            "n_markets_with_fills": metrics.n_markets_with_fills,
            "longest_losing_streak": metrics.longest_losing_streak,
            "methodology": methodology,
            "methodology_state": methodology_state,
            "methodology_category": methodology_category,
            "methodology_score": methodology_score,
            "_w_start": w_start,
        })

    wf_results: list[WalkForwardResult] = []
    for cand in candidates:
        oos_windows = sorted(per_cand[id(cand)], key=lambda w: w["_w_start"])
        for w in oos_windows:
            w.pop("_w_start", None)
        if not oos_windows:
            continue
        recent = oos_windows[-1]
        agg_metrics = BacktestMetrics(
            pnl=sum(w["pnl"] for w in oos_windows) / len(oos_windows),
            sharpe=sum(w["sharpe"] for w in oos_windows) / len(oos_windows),
            max_dd=min(w["max_dd"] for w in oos_windows),
            n_trades=sum(int(w["n_trades"]) for w in oos_windows),
            expectancy_usdc=sum(float(w.get("expectancy_usdc", 0.0)) for w in oos_windows) / len(oos_windows),
            fill_rate=sum(float(w.get("fill_rate", 0.0)) for w in oos_windows) / len(oos_windows),
            n_orders=sum(int(w.get("n_orders", 0)) for w in oos_windows),
            n_fills=sum(int(w.get("n_fills", 0)) for w in oos_windows),
            n_markets=max(int(w.get("n_markets", 1)) for w in oos_windows),
            n_markets_with_fills=max(int(w.get("n_markets_with_fills", 0)) for w in oos_windows),
            longest_losing_streak=max(int(w.get("longest_losing_streak", 0)) for w in oos_windows),
        )
        methodology, methodology_state, methodology_category, methodology_score = _methodology_for_metrics(
            agg_metrics,
            min_trades=args.min_trades_floor,
            min_fill_rate=args.min_fill_rate,
            min_markets_with_fills=args.min_markets_with_fills,
        )
        wf_results.append(WalkForwardResult(
            params=cand.params,
            oos_windows=oos_windows,
            oos_mean_sharpe=agg_metrics.sharpe,
            oos_mean_pnl=agg_metrics.pnl,
            recent_oos_sharpe=recent["sharpe"],
            recent_oos_pnl=recent["pnl"],
            is_sharpe=cand.metrics.sharpe,
            methodology=methodology,
            methodology_state=methodology_state,
            methodology_category=methodology_category,
            methodology_score=methodology_score,
        ))

    if not wf_results:
        print(json.dumps({"ok": False, "error": "walk_forward_empty"}))
        return 4

    # 4) Pick winner by OOS methodology first, then current-regime edge.
    wf_results.sort(
        key=lambda w: (
            w.methodology_score,
            w.recent_oos_pnl,
            w.oos_mean_pnl,
            w.recent_oos_sharpe,
            w.oos_mean_sharpe,
        ),
        reverse=True,
    )
    winner = wf_results[0]
    new_state, category = decide_oos(winner)

    warnings: list[str] = []
    # Sanity check: if every grid point yielded an identical (pnl, n_trades)
    # tuple, the parameters didn't actually affect the strategy. Likely the
    # param names in the MD don't match the strategy *Config fields.
    unique_results = {(round(g.metrics.pnl, 4), g.metrics.n_trades) for g in grid}
    if len(grid) > 1 and len(unique_results) == 1:
        warnings.append("grid_metrics_identical")

    out = {
        "ok": True,
        "slug": args.slug,
        "venue": "polymarket",
        "warnings": warnings,
        "grid_size": len(grid),
        "wf_candidates": len(wf_results),
        "candidate_ranking": [
            {
                "params": c.params,
                "score": c.methodology_score,
                "state": c.methodology_state,
                "category": c.methodology_category,
                "pnl": c.metrics.pnl,
                "expectancy_usdc": c.metrics.expectancy_usdc,
                "fill_rate": c.metrics.fill_rate,
                "n_trades": c.metrics.n_trades,
                "sharpe": c.metrics.sharpe,
                "max_dd": c.metrics.max_dd,
            }
            for c in candidates
        ],
        "best_params": winner.params,
        "best_is_sharpe": winner.is_sharpe,
        "best_oos_mean_sharpe": winner.oos_mean_sharpe,
        "best_oos_mean_pnl": winner.oos_mean_pnl,
        "best_recent_oos_sharpe": winner.recent_oos_sharpe,
        "best_recent_oos_pnl": winner.recent_oos_pnl,
        "best_methodology_score": winner.methodology_score,
        "best_methodology": winner.methodology,
        "best_methodology_state": winner.methodology_state,
        "best_methodology_category": winner.methodology_category,
        "oos_windows": winner.oos_windows,
        "decision_new_state": new_state,
        "decision_rejection_category": category,
        "applied": False,
    }
    output_path = args.output_file or default_output_path(
        slug=args.slug,
        data_start=data_start.date().isoformat(),
        data_end=data_end.date().isoformat(),
    )

    # Don't apply the transition if grid was effectively non-parametric.
    if "grid_metrics_identical" in warnings:
        out["applied"] = False
        out["decision_new_state"] = "REJECTED"
        out["decision_rejection_category"] = "param_space_inert"
        out = write_optimizer_artifact(out, output_path=output_path)
        print(json.dumps(out))
        return 0

    if not args.no_transition and h.state != new_state:
        try:
            lifecycle.transition(
                slug=args.slug,
                to_state=new_state,
                reason=(
                    f"optimize: best_params={winner.params} "
                    f"score={winner.methodology_score:.1f} "
                    f"oos_mean_sharpe={winner.oos_mean_sharpe:.3f} "
                    f"recent_oos_pnl=${winner.recent_oos_pnl:.2f} "
                    f"exp={winner.methodology.get('expectancy_usdc', 0.0):.4f}"
                ),
                actor=args.actor,
                rejection_category=category or None,
                db_path=args.db,
            )
            out["applied"] = True
        except Exception as exc:
            out["error"] = str(exc)

    out = write_optimizer_artifact(out, output_path=output_path)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
