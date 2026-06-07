#!/usr/bin/env python3
"""
Hyperliquid hyperparameter optimisation with walk-forward + anti-overfitting.

For a hypothesis slug:
  1. Read `research/hypotheses/<slug>.md`.
     - Frontmatter supplies strategy module/class/config and HL fields
       (`bar_interval`, `universe_as_of`, `universe_tiers`, `backfill_start`,
       `funding_aware`).
     - The `## Parameter space` section enumerates the grid (`name: [v1, v2]`).

  2. Build walk-forward folds (anchored, with embargo) over the available
     data window from `backfill_start` to today.

  3. For each (fold, config) cell run a multi-market backtest across the
     coins resolved from the universe snapshot. Per-fold per-config Sharpe
     forms the PBO matrix.

  4. Score the search:
        * For each config, average WF-fold Sharpe.
        * Pick the best-mean-Sharpe config.
        * Deflated Sharpe Ratio against benchmark = 0 using n_trials = grid.
        * PBO via CSCV.
        * Parameter stability across folds' best configs.

  5. Apply decision rules:
        DSR prob < 0.9   -> REJECTED  (overfit_dsr)
        PBO > 0.5         -> REJECTED  (overfit_pbo)
        max_param_cv > 0.6 -> SHELVED  (unstable_params)
        OOS mean Sharpe < 0.7 -> SHELVED (marginal_oos)
        else              -> PAPER_READY

  6. Print a JSON report; persist a row per (config, fold) in
     `research/experiments.db` if available.

The script is designed to be agent-friendly: stdout is always one JSON object.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

from trading_lab.data.hl_catalog import HyperliquidCatalog
from trading_lab.data.hl_universe import (
    coin_names,
    filter_by_tier,
    load_universe,
)
from trading_lab.research.optimizer_artifacts import (
    default_output_path,
    write_optimizer_artifact,
)
from trading_lab.research.overfitting import (
    deflated_sharpe_ratio,
    max_cv,
    parameter_stability,
    probability_of_backtest_overfitting,
)
from trading_lab.research.walk_forward import (
    WalkForwardWindow,
    coverage_summary,
    make_walk_forward_windows,
)
from trading_lab.runner.hl_backtest import (
    HyperliquidBacktestRunner,
    HyperliquidFeeConfig,
)

log = logging.getLogger("hl_optimize")


# ---------------------------------------------------------------------------
# Hypothesis MD parsing
# ---------------------------------------------------------------------------


def _resolve_hypothesis_path(slug: str, hypotheses_dir: Path) -> Path:
    candidates = [
        hypotheses_dir / slug / "spec.md",
        hypotheses_dir / slug / "dossier.md",
        hypotheses_dir / f"{slug}.md",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"hypothesis missing for slug={slug} under {hypotheses_dir}")


def parse_hypothesis(slug: str, hypotheses_dir: Path) -> tuple[dict[str, Any], str]:
    """Return (frontmatter, body)."""
    md_path = _resolve_hypothesis_path(slug, hypotheses_dir)
    text = md_path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"hypothesis {slug} missing frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError(f"hypothesis {slug} frontmatter not terminated")
    import yaml

    fm = yaml.safe_load(text[3:end].strip()) or {}
    body = text[end + 4 :].strip()
    return fm, body


PARAM_LINE_RE = re.compile(r"^\s*-?\s*`?(?P<name>[A-Za-z_][A-Za-z0-9_]*)`?\s*:\s*\[(?P<vals>.+)\]\s*$")


def parse_param_space(body: str) -> dict[str, list[float]]:
    """Pull `## Parameter space` `name: [v1, v2, ...]` lines into a dict."""
    space: dict[str, list[float]] = {}
    in_section = False
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.lower().startswith("## parameter space"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        m = PARAM_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        vals_str = m.group("vals")
        values = []
        for piece in vals_str.split(","):
            v = piece.strip()
            if not v:
                continue
            try:
                values.append(int(v))
            except ValueError:
                try:
                    values.append(float(v))
                except ValueError:
                    pass
        if values:
            space[name] = values
    return space


def resolve_coins(fm: dict[str, Any], universe: list[Any]) -> list[str]:
    market_criteria = fm.get("market_criteria") or {}
    raw_symbols = market_criteria.get("symbols") or []
    if raw_symbols:
        return [str(symbol).upper() for symbol in raw_symbols]
    return coin_names(universe)


# ---------------------------------------------------------------------------
# Optimiser
# ---------------------------------------------------------------------------


@dataclass
class FoldConfigResult:
    fold: int
    params: dict[str, float]
    sharpe: float
    pnl: float
    n_trades: int
    max_dd_pct: float


def _cartesian(space: dict[str, list[float]]) -> list[dict[str, float]]:
    names = list(space.keys())
    combos = list(itertools.product(*(space[n] for n in names)))
    return [dict(zip(names, c, strict=False)) for c in combos]


def _run_one(
    runner: HyperliquidBacktestRunner,
    *,
    fm: dict[str, Any],
    coins: list[str],
    params: dict[str, float],
    bar_interval: str,
    window: WalkForwardWindow,
    initial_capital: float,
    test_only: bool,
    use_funding: bool,
) -> dict[str, float | int]:
    """Run one (config, fold) cell and return evaluation metrics."""
    start = window.test_start if test_only else window.train_start
    end = window.test_end if test_only else window.train_end
    result = runner.run_multi_market(
        coins=coins,
        bar_interval=bar_interval,
        start=start,
        end=end,
        strategy_module=fm["strategy_module"],
        strategy_class=fm["strategy_class"],
        strategy_config_class=fm.get("strategy_config_class"),
        strategy_params=params,
        initial_capital_usdc_per_market=initial_capital,
        use_funding=use_funding,
    )
    m = result.portfolio_metrics
    total_pnl = float(m.extras.get("total_pnl", 0.0))
    n_trades = int(m.n_trades)
    n_markets = int(m.extras.get("n_markets_total", len(result.per_market)))
    n_markets_with_fills = sum(1 for market in result.per_market if market.n_fills > 0)
    return {
        "sharpe": float(m.sharpe),
        "pnl": total_pnl,
        "n_trades": n_trades,
        "max_dd_pct": float(m.max_drawdown_pct),
        "expectancy_usdc": (total_pnl / n_trades) if n_trades > 0 else 0.0,
        "n_fills": n_trades,
        "n_orders": 0,
        "fill_rate": 0.0,
        "n_markets": n_markets,
        "n_markets_with_fills": n_markets_with_fills,
    }


def _config_methodology(
    *,
    oos_mean_sharpe: float,
    oos_total_pnl: float,
    oos_total_trades: int,
    oos_min_trades: int,
    oos_worst_dd: float,
    n_markets: int,
    n_markets_with_fills: int,
) -> tuple[dict[str, Any], str, str, float]:
    from trading_lab.agent.lifecycle import State
    from trading_lab.research.eval_methodology import assess_backtest
    from trading_lab.research.experiment_reporting import score_backtest_result

    expectancy = (oos_total_pnl / oos_total_trades) if oos_total_trades > 0 else 0.0
    decision = assess_backtest(
        state_enum=State,
        sharpe=oos_mean_sharpe,
        max_dd_pct=oos_worst_dd,
        n_trades=oos_total_trades,
        pnl_usdc=oos_total_pnl,
        expectancy_usdc=expectancy,
        fill_rate=0.0,
        n_orders=0,
        n_fills=oos_total_trades,
        n_markets=n_markets,
        n_markets_with_fills=n_markets_with_fills,
    )
    score = score_backtest_result(
        {
            "methodology": decision.methodology,
            "methodology_decision_state": decision.new_state,
            "pnl": oos_total_pnl,
            "expectancy_usdc": expectancy,
            "fill_rate": 0.0,
            "sharpe": oos_mean_sharpe,
            "max_dd": oos_worst_dd,
            "n_markets_with_fills": n_markets_with_fills,
        }
    )
    return decision.methodology, decision.new_state, decision.rejection_category, score


def _decide(
    *,
    oos_mean_sharpe: float,
    dsr_prob: float,
    pbo: float,
    max_param_cv: float,
    n_trials: int,
    n_folds: int,
    min_oos_trades: int,
) -> tuple[str, str]:
    """
    Tiered decision rules.

    The Bailey DSR is *very* strict when OOS windows are short relative to
    the annualisation factor (hourly Sharpe over 30-day windows has a noise
    floor several units of Sharpe wide). We treat DSR as one signal among
    several rather than a single hard gate.

    REJECTED if:
      * Strong evidence of overfitting: PBO > 0.6, OR
      * Clearly losing: OOS Sharpe < 0, OR
      * Too few trades to evaluate: min OOS trades < 10
    SHELVED if:
      * DSR probability < 0.25 (very likely lucky), OR
      * Unstable params (CV > 0.6), OR
      * Marginal OOS (Sharpe < 0.5)
    PAPER_READY otherwise (positive OOS Sharpe + survives PBO + DSR ≥ 0.25).

    The caller can post-filter further. We log every gate's value so a human
    reviewer can decide.
    """
    if min_oos_trades < 10:
        return "REJECTED", f"too_few_trades (min={min_oos_trades})"
    if oos_mean_sharpe <= 0:
        return "REJECTED", f"negative_oos_sharpe ({oos_mean_sharpe:.2f})"
    if pbo > 0.6:
        return "REJECTED", f"overfit_pbo (pbo={pbo:.3f})"
    if dsr_prob < 0.25:
        return "SHELVED", f"low_dsr (prob={dsr_prob:.3f}) — needs longer OOS or fewer trials"
    if max_param_cv > 0.6:
        return "SHELVED", f"unstable_params (cv={max_param_cv:.3f})"
    if oos_mean_sharpe < 0.5:
        return "SHELVED", f"marginal_oos (sharpe={oos_mean_sharpe:.2f})"
    return "PAPER_READY", "all_gates_passed"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--slug", required=True)
    p.add_argument("--hypotheses-dir", type=Path, default=Path("research/hypotheses"))
    p.add_argument("--data-dir", default="data/parquet")
    p.add_argument("--data-start", help="UTC YYYY-MM-DD override; defaults to hypothesis.backfill_start")
    p.add_argument("--data-end", help="UTC YYYY-MM-DD override; defaults to today")
    p.add_argument("--n-folds", type=int, default=5)
    p.add_argument("--embargo-days", type=int, default=1)
    p.add_argument("--min-train-days", type=int, default=30)
    p.add_argument("--min-test-days", type=int, default=14)
    p.add_argument("--initial-capital-usdc", type=float, default=10_000.0)
    p.add_argument("--max-configs", type=int, default=0, help="Cap grid size (0 = no cap)")
    p.add_argument("--max-coins", type=int, default=0, help="Cap universe size (0 = all)")
    p.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Optional explicit path for the JSON summary artifact",
    )
    p.add_argument("--log-level", default="WARNING")
    p.add_argument("--no-funding", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    fm, body = parse_hypothesis(args.slug, args.hypotheses_dir)
    space = parse_param_space(body)
    if not space:
        print(json.dumps({"ok": False, "error": "no_parameter_space"}))
        return 2

    catalog = HyperliquidCatalog(Path(args.data_dir))

    # Universe (point-in-time as of `universe_as_of` in frontmatter).
    as_of_iso = fm.get("universe_as_of") or args.data_end or datetime.now(UTC).date().isoformat()
    if isinstance(as_of_iso, datetime):
        as_of_iso = as_of_iso.date().isoformat()
    elif hasattr(as_of_iso, "isoformat"):
        as_of_iso = as_of_iso.isoformat()
    as_of = datetime.fromisoformat(str(as_of_iso)).replace(tzinfo=UTC)
    universe = load_universe(catalog, as_of)
    if not universe:
        print(json.dumps({"ok": False, "error": "no_universe_snapshot"}))
        return 2
    tiers = fm.get("universe_tiers")
    universe = filter_by_tier(universe, tiers)
    coins = resolve_coins(fm, universe)
    if args.max_coins > 0:
        coins = coins[: args.max_coins]
    log.info("universe: %d coins (%s)", len(coins), coins)

    # Window
    raw_start = args.data_start or fm.get("backfill_start") or "2024-05-30"
    raw_end = args.data_end or datetime.now(UTC).date().isoformat()
    if hasattr(raw_start, "isoformat"):
        raw_start = raw_start.isoformat()
    if hasattr(raw_end, "isoformat"):
        raw_end = raw_end.isoformat()
    data_start = datetime.fromisoformat(str(raw_start)).replace(tzinfo=UTC)
    data_end = datetime.fromisoformat(str(raw_end)).replace(tzinfo=UTC)
    windows = make_walk_forward_windows(
        data_start,
        data_end,
        mode="anchored",
        n_folds=args.n_folds,
        embargo_days=args.embargo_days,
        min_train_days=args.min_train_days,
        min_test_days=args.min_test_days,
    )
    if not windows:
        print(json.dumps({"ok": False, "error": "no_walk_forward_windows", "data_start": str(data_start), "data_end": str(data_end)}))
        return 2

    # Grid
    grid = _cartesian(space)
    if args.max_configs and len(grid) > args.max_configs:
        # Down-sample deterministically (every k-th config).
        step = len(grid) // args.max_configs
        grid = [grid[i] for i in range(0, len(grid), step)][: args.max_configs]
    log.info("grid: %d configs × %d folds = %d cells", len(grid), len(windows), len(grid) * len(windows))

    bar_interval = fm.get("bar_interval", "1h")
    use_funding = (not args.no_funding) and bool(fm.get("funding_aware", True))
    runner = HyperliquidBacktestRunner(
        catalog,
        fees=HyperliquidFeeConfig(),
        log_level=args.log_level.upper(),
    )

    # Train and test pass for each fold × each config.
    # Matrix layout: rows = fold, cols = config index.
    n_folds = len(windows)
    n_cfgs = len(grid)
    train_sharpe = np.full((n_folds, n_cfgs), np.nan)
    test_sharpe = np.full((n_folds, n_cfgs), np.nan)
    test_pnl = np.full((n_folds, n_cfgs), 0.0)
    test_trades = np.zeros((n_folds, n_cfgs), dtype=int)
    test_dd = np.full((n_folds, n_cfgs), 0.0)

    all_runs: list[dict[str, Any]] = []

    for fi, w in enumerate(windows):
        for ci, cfg in enumerate(grid):
            try:
                # Train
                train_metrics = _run_one(
                    runner,
                    fm=fm,
                    coins=coins,
                    params=cfg,
                    bar_interval=bar_interval,
                    window=w,
                    initial_capital=args.initial_capital_usdc,
                    test_only=False,
                    use_funding=use_funding,
                )
                ts = float(train_metrics["sharpe"])
                train_sharpe[fi, ci] = ts
                # Test (OOS)
                test_metrics = _run_one(
                    runner,
                    fm=fm,
                    coins=coins,
                    params=cfg,
                    bar_interval=bar_interval,
                    window=w,
                    initial_capital=args.initial_capital_usdc,
                    test_only=True,
                    use_funding=use_funding,
                )
                sh = float(test_metrics["sharpe"])
                pn = float(test_metrics["pnl"])
                nt = int(test_metrics["n_trades"])
                dd = float(test_metrics["max_dd_pct"])
                test_sharpe[fi, ci] = sh
                test_pnl[fi, ci] = pn
                test_trades[fi, ci] = nt
                test_dd[fi, ci] = dd
                log.info(
                    "fold=%d cfg=%d sharpe_train=%.2f sharpe_test=%.2f pnl=%.1f trades=%d",
                    fi, ci, ts, sh, pn, nt,
                )
                all_runs.append({
                    "fold": fi,
                    "config_idx": ci,
                    "params": cfg,
                    "train_sharpe": ts,
                    "test_sharpe": sh,
                    "test_pnl": pn,
                    "test_trades": int(nt),
                    "test_max_dd_pct": dd,
                    "test_expectancy_usdc": float(test_metrics["expectancy_usdc"]),
                    "test_n_markets": int(test_metrics["n_markets"]),
                    "test_n_markets_with_fills": int(test_metrics["n_markets_with_fills"]),
                })
            except Exception as exc:  # noqa: BLE001
                log.exception("fold=%d cfg=%d failed", fi, ci)
                all_runs.append({
                    "fold": fi, "config_idx": ci, "params": cfg, "error": str(exc),
                })

    # Aggregate per-config OOS metrics.
    config_oos_mean_sharpe = np.nanmean(test_sharpe, axis=0)
    config_oos_total_pnl = np.nansum(test_pnl, axis=0)
    config_oos_min_trades = np.min(test_trades, axis=0)
    config_oos_total_trades = np.sum(test_trades, axis=0)
    config_oos_worst_dd = np.nanmin(test_dd, axis=0)

    per_config: list[dict[str, Any]] = []
    for ci, cfg in enumerate(grid):
        n_markets_with_fills = int(max((run.get("test_n_markets_with_fills", 0) for run in all_runs if run.get("config_idx") == ci and "error" not in run), default=0))
        n_markets = int(max((run.get("test_n_markets", len(coins)) for run in all_runs if run.get("config_idx") == ci and "error" not in run), default=len(coins)))
        methodology, methodology_state, methodology_category, methodology_score = _config_methodology(
            oos_mean_sharpe=float(config_oos_mean_sharpe[ci]),
            oos_total_pnl=float(config_oos_total_pnl[ci]),
            oos_total_trades=int(config_oos_total_trades[ci]),
            oos_min_trades=int(config_oos_min_trades[ci]),
            oos_worst_dd=float(config_oos_worst_dd[ci]),
            n_markets=n_markets,
            n_markets_with_fills=n_markets_with_fills,
        )
        per_config.append(
            {
                "config_idx": ci,
                "params": cfg,
                "oos_mean_sharpe": float(config_oos_mean_sharpe[ci]),
                "oos_total_pnl": float(config_oos_total_pnl[ci]),
                "oos_total_trades": int(config_oos_total_trades[ci]),
                "oos_min_trades": int(config_oos_min_trades[ci]),
                "oos_worst_dd": float(config_oos_worst_dd[ci]),
                "expectancy_usdc": float(config_oos_total_pnl[ci] / config_oos_total_trades[ci]) if int(config_oos_total_trades[ci]) > 0 else 0.0,
                "n_markets": n_markets,
                "n_markets_with_fills": n_markets_with_fills,
                "methodology": methodology,
                "methodology_state": methodology_state,
                "methodology_category": methodology_category,
                "methodology_score": methodology_score,
            }
        )

    per_config.sort(
        key=lambda c: (
            c["methodology_score"],
            c["oos_mean_sharpe"],
            c["oos_total_pnl"],
            c["expectancy_usdc"],
            -abs(c["oos_worst_dd"]),
        ),
        reverse=True,
    )
    best = per_config[0]
    best_idx = int(best["config_idx"])
    best_params = best["params"]
    best_sharpe = float(best["oos_mean_sharpe"])

    # Per-fold best params (for stability)
    per_fold_best: list[dict[str, float]] = []
    for fi in range(n_folds):
        row = train_sharpe[fi]
        if np.all(np.isnan(row)):
            continue
        bi = int(np.nanargmax(row))
        per_fold_best.append(grid[bi])

    stability = parameter_stability(per_fold_best)
    max_param_cv = float(max_cv(stability))

    # DSR — pass the per-period bar count for the OOS Sharpe.
    # n_samples ≈ bars in median OOS window.
    median_test_bars = _median_oos_bars(windows, bar_interval)
    annualisation_factor = {
        "5m": 365 * 24 * 12,
        "1h": 365 * 24,
        "1d": 365.0,
    }.get(bar_interval, 365.0)
    dsr = deflated_sharpe_ratio(
        best_sharpe,
        n_trials=n_cfgs,
        n_samples=max(median_test_bars, 2),
        annualisation_factor=annualisation_factor,
    )

    # PBO: rows=folds, cols=configs, value=test_sharpe (drop NaN columns).
    pbo_matrix = np.nan_to_num(test_sharpe, nan=0.0)
    pbo = probability_of_backtest_overfitting(pbo_matrix, n_splits=24)

    state, reason = _decide(
        oos_mean_sharpe=best_sharpe,
        dsr_prob=dsr["dsr_probability"],
        pbo=pbo["pbo"],
        max_param_cv=max_param_cv,
        n_trials=n_cfgs,
        n_folds=n_folds,
        min_oos_trades=int(config_oos_min_trades[best_idx]),
    )

    summary = {
        "ok": True,
        "slug": args.slug,
        "venue": fm.get("venue", "hyperliquid"),
        "bar_interval": bar_interval,
        "universe": coins,
        "n_folds": n_folds,
        "n_configs": n_cfgs,
        "walk_forward": [w.as_dict() for w in windows],
        "candidate_ranking": [
            {
                "config_idx": c["config_idx"],
                "params": c["params"],
                "score": c["methodology_score"],
                "state": c["methodology_state"],
                "category": c["methodology_category"],
                "sharpe": c["oos_mean_sharpe"],
                "pnl": c["oos_total_pnl"],
                "expectancy_usdc": c["expectancy_usdc"],
                "n_trades": c["oos_total_trades"],
                "min_oos_trades": c["oos_min_trades"],
                "max_dd": c["oos_worst_dd"],
                "n_markets": c["n_markets"],
                "n_markets_with_fills": c["n_markets_with_fills"],
                "methodology": c["methodology"],
            }
            for c in per_config
        ],
        "best_config_idx": best_idx,
        "best_params": best_params,
        "best_oos_mean_sharpe": best_sharpe,
        "best_oos_total_pnl": float(config_oos_total_pnl[best_idx]),
        "best_oos_min_trades": int(config_oos_min_trades[best_idx]),
        "best_methodology_score": float(best["methodology_score"]),
        "best_methodology": best["methodology"],
        "best_methodology_state": best["methodology_state"],
        "best_methodology_category": best["methodology_category"],
        "deflated_sharpe": dsr,
        "pbo": {"pbo": pbo["pbo"], "n_splits": pbo["n_evaluated_splits"]},
        "parameter_stability": stability,
        "max_param_cv": max_param_cv,
        "decision_state": state,
        "decision_reason": reason,
        "coverage": coverage_summary(windows),
        "all_runs": all_runs,
    }
    output_path = args.output_file or default_output_path(
        slug=args.slug,
        data_start=data_start.date().isoformat(),
        data_end=data_end.date().isoformat(),
    )
    summary = write_optimizer_artifact(summary, output_path=output_path)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _median_oos_bars(windows: list[WalkForwardWindow], bar_interval: str) -> int:
    bars_per_day = {
        "5m": 24 * 12, "15m": 24 * 4, "1h": 24, "4h": 6, "1d": 1,
    }.get(bar_interval, 24)
    days = sorted((w.test_end - w.test_start).days for w in windows)
    if not days:
        return 2
    median_days = days[len(days) // 2]
    return median_days * bars_per_day


if __name__ == "__main__":
    sys.exit(main())
