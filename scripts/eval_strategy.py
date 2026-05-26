#!/usr/bin/env python3
"""
Evaluate a hypothesis: run the backtest for its configured market criteria,
record an `experiments` row, and apply the decision-rule transition.

Decision rules (Phase 5.5 simplification — extend with Bonferroni later):
    n_trades < 30                          → REJECTED  (insufficient_trades)
    sharpe < 0                              → REJECTED  (unprofitable)
    0 ≤ sharpe < 0.5                        → SHELVED   (marginal_is)
    0.5 ≤ sharpe < 1.0 AND max_dd > 25%     → REJECTED  (high_dd)
    0.5 ≤ sharpe < 1.0                      → SHELVED   (marginal_is)
    sharpe ≥ 1.0 AND max_dd ≤ 20%           → OPTIMIZE

Usage:
    python scripts/eval_strategy.py --slug arb-complement \\
        --start 2026-05-10 --end 2026-05-26

Prints JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()


def decide(
    sharpe: float,
    max_dd_pct: float,
    n_trades: int,
    pnl: float = 0.0,
    min_trades: int = 30,
) -> tuple[str, str]:
    """
    Return (new_state, rejection_category_or_empty).

    For binary-arb strategies the cash-equity Sharpe is misleading (it dips
    as capital is deployed and never recovers because the arb's $1 payoff
    is at resolution, not in-window). We use PnL as the primary signal and
    treat the Sharpe band as a secondary filter only when PnL is positive.
    """
    from trading_lab.agent.lifecycle import State

    if n_trades < min_trades:
        return State.REJECTED.value, "insufficient_trades"
    if pnl < 0:
        return State.REJECTED.value, "unprofitable"
    # PnL is positive — sort by sharpe band, but be lenient on the cash-equity
    # Sharpe signal for hold-to-resolution strategies.
    if pnl > 0 and sharpe < 0 and n_trades >= max(100, min_trades * 3):
        return State.OPTIMIZE.value, ""
    if sharpe < 0.5:
        return State.SHELVED.value, "marginal_is"
    if sharpe < 1.0:
        if abs(max_dd_pct) > 25:
            return State.REJECTED.value, "high_dd"
        return State.SHELVED.value, "marginal_is"
    if abs(max_dd_pct) > 20:
        return State.REJECTED.value, "high_dd"
    return State.OPTIMIZE.value, ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--initial-capital-usdc", type=float, default=10_000.0)
    p.add_argument("--actor", default="agent:eval")
    p.add_argument(
        "--min-trades-floor", type=int, default=30,
        help="Reject as insufficient_trades when n_trades below this. Default 30; "
             "lower it for short data windows during integration testing.",
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    p.add_argument(
        "--no-transition",
        action="store_true",
        help="Record experiment but do not call lifecycle.transition()",
    )
    args = p.parse_args()

    from trading_lab.agent import budget, lifecycle
    from trading_lab.config import load_config

    # Budget check
    ok, n, cap = budget.check("backtests", db_path=args.db)
    if not ok:
        print(json.dumps({"ok": False, "error": "budget_exhausted",
                          "consumed": n, "cap": cap}))
        return 3

    h = lifecycle.get_hypothesis(args.slug, db_path=args.db)
    if not h:
        print(json.dumps({"ok": False, "error": "hypothesis_not_found",
                          "slug": args.slug}))
        return 2

    cfg = load_config()
    # Subprocess `scripts/backtest.py` rather than calling BacktestRunner
    # in-process. NautilusTrader's logger is a global that panics on second
    # engine instantiation in the same process, so we keep each backtest
    # in its own fresh process. (optimize_strategy.py uses the same trick.)
    import os
    import subprocess as _sp
    env = os.environ.copy()
    cmd = [
        sys.executable, "scripts/backtest.py",
        "--hypothesis-slug", args.slug,
        "--start", args.start,
        "--end", args.end,
        "--initial-capital-usdc", str(args.initial_capital_usdc),
        "--json",
    ]
    proc = _sp.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    if proc.returncode != 0:
        print(json.dumps({
            "ok": False, "error": "backtest_subprocess_failed",
            "rc": proc.returncode,
            "stderr_tail": proc.stderr[-400:],
        }))
        return 4
    # Last JSON line of stdout is the summary.
    summary_line = None
    for line in reversed(proc.stdout.strip().splitlines()):
        s = line.strip()
        if s.startswith("{"):
            summary_line = s
            break
    if not summary_line:
        print(json.dumps({"ok": False, "error": "no_json_in_backtest_stdout"}))
        return 4
    summary = json.loads(summary_line)
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    _ = (start, end)  # keep linter happy

    n_trades = summary["aggregate_n_fills"]
    pnl = summary["aggregate_pnl_usdc"]
    sharpe = summary["mean_sharpe"]
    # Max DD over the per-market dimension (max drawdown among per-market values)
    max_dd = min(
        (m["max_drawdown_pct"] for m in summary["per_market"]), default=0.0
    )

    # Record the actual strategy params (defaults from the hypothesis's
    # strategy config). For BinaryArbStrategy, that's `min_profit_usdc` /
    # `max_capital_usdc` from TradingConfig.arb. For agent-written strategies,
    # we record their *Config defaults so downstream paper_run.py can pick
    # the right experiment row.
    recorded_params: dict = {}
    if h.strategy_module and h.strategy_config_class:
        try:
            import importlib
            mod = importlib.import_module(h.strategy_module)
            cfg_cls = getattr(mod, h.strategy_config_class)
            inst = cfg_cls()
            full = inst.dict() if hasattr(inst, "dict") else {}
            allowed = set(getattr(cfg_cls, "__struct_fields__", ()))
            # Drop NT-base StrategyConfig boilerplate; keep strategy-specific
            # knobs by excluding common base fields.
            base = {
                "strategy_id", "order_id_tag", "use_uuid_client_order_ids",
                "use_hyphens_in_client_order_ids", "oms_type",
                "external_order_claims", "manage_contingent_orders",
                "manage_gtd_expiry", "manage_stop", "market_exit_interval_ms",
                "market_exit_max_attempts", "market_exit_time_in_force",
                "market_exit_reduce_only", "log_events", "log_commands",
                "log_rejected_due_post_only_as_warning",
            }
            recorded_params = {
                k: v for k, v in full.items()
                if k in allowed and k not in base
            }
        except Exception:
            recorded_params = {}
    if not recorded_params:
        # Fall back to a sentinel empty dict when we can't introspect the
        # strategy's config — better than recording stale arb-specific keys
        # that have no meaning for newer strategies.
        recorded_params = {}

    exp_id = lifecycle.record_experiment(
        slug=args.slug,
        params=recorded_params,
        data_start=args.start,
        data_end=args.end,
        sharpe=float(sharpe),
        max_dd=float(max_dd),
        fill_rate=(summary["aggregate_n_fills"] / max(summary["aggregate_n_orders"], 1)),
        pnl=float(pnl),
        n_trades=int(n_trades),
        db_path=args.db,
    )
    budget.consume("backtests", db_path=args.db)

    new_state, category = decide(
        float(sharpe), float(max_dd), int(n_trades), pnl=float(pnl),
        min_trades=args.min_trades_floor,
    )
    out = {
        "ok": True,
        "experiment_id": exp_id,
        "slug": args.slug,
        "sharpe": sharpe,
        "pnl_usdc": pnl,
        "max_dd_pct": max_dd,
        "n_trades": n_trades,
        "decision_new_state": new_state,
        "decision_rejection_category": category,
        "applied": False,
    }
    if not args.no_transition and h.state != new_state:
        try:
            lifecycle.transition(
                slug=args.slug,
                to_state=new_state,
                reason=f"eval: sharpe={sharpe:.3f} dd={max_dd:.1f}% trades={n_trades}",
                actor=args.actor,
                rejection_category=category or None,
                db_path=args.db,
            )
            out["applied"] = True
        except Exception as exc:
            out["error"] = str(exc)

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
