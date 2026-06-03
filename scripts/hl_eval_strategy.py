#!/usr/bin/env python3
"""
Evaluate a Hyperliquid hypothesis: run the HL-native backtest, record an
`experiments` row, and apply the decision-rule transition.

This mirrors `scripts/eval_strategy.py` but uses `scripts/hl_backtest.py`
instead of the Polymarket-first generic backtest path.

Decision rules intentionally match the generic eval path:
    n_trades < 30                          → REJECTED  (insufficient_trades)
    pnl < 0                                → REJECTED  (unprofitable)
    pnl > 0 AND sharpe < 0 AND
        n_trades >= max(100, 3*floor)      → OPTIMIZE  (hold-to-resolution artefact)
    sharpe < 0.5                           → SHELVED   (marginal_is)
    0.5 ≤ sharpe < 1.0 AND |max_dd| > 25%  → REJECTED  (high_dd)
    0.5 ≤ sharpe < 1.0                     → SHELVED   (marginal_is)
    sharpe ≥ 1.0 AND |max_dd| > 20%        → REJECTED  (high_dd)
    otherwise                              → OPTIMIZE

Prints JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    from trading_lab.agent.lifecycle import State

    if n_trades < min_trades:
        return State.REJECTED.value, "insufficient_trades"
    if pnl < 0:
        return State.REJECTED.value, "unprofitable"
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


def _last_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("no_json_output")

    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    decoder = json.JSONDecoder()
    starts = [idx for idx, ch in enumerate(stdout) if ch in "[{"]
    for start in reversed(starts):
        try:
            parsed, _end = decoder.raw_decode(stdout[start:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no_json_output")


def _read_hypothesis_frontmatter(slug: str) -> dict[str, Any]:
    path = Path("research/hypotheses") / f"{slug}.md"
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"hypothesis {slug} has no YAML frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError(f"hypothesis {slug} frontmatter not terminated")
    import yaml

    return yaml.safe_load(text[3:end].strip()) or {}


def _extract_result_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode")
    result = payload.get("result") or {}
    if mode == "single":
        metrics = (result.get("metrics") or {}).copy()
        n_orders = int(result.get("n_orders", 0))
        n_fills = int(result.get("n_fills", 0))
        n_markets = 1
        n_markets_with_fills = 1 if n_fills > 0 else 0
    else:
        metrics = (result.get("portfolio_metrics") or {}).copy()
        per_market = result.get("per_market") or []
        n_orders = sum(int(m.get("n_orders", 0)) for m in per_market)
        n_fills = sum(int(m.get("n_fills", 0)) for m in per_market)
        n_markets = int(result.get("n_markets", len(per_market)))
        n_markets_with_fills = int(
            result.get(
                "n_markets_with_fills",
                sum(1 for m in per_market if int(m.get("n_fills", 0)) > 0),
            )
        )

    extras = metrics.get("extras") or {}
    pnl = extras.get("total_pnl")
    if pnl is None:
        pnl = float(metrics.get("price_pnl", 0.0)) + float(metrics.get("funding_pnl", 0.0))

    return {
        "sharpe": float(metrics.get("sharpe", 0.0)),
        "max_dd_pct": float(metrics.get("max_drawdown_pct", 0.0)),
        "n_trades": int(metrics.get("n_trades", 0)),
        "pnl_usdc": float(pnl),
        "fill_rate": (n_fills / max(n_orders, 1)),
        "n_orders": n_orders,
        "n_fills": n_fills,
        "n_markets": n_markets,
        "n_markets_with_fills": n_markets_with_fills,
    }


def _recorded_params(strategy_module: str, strategy_config_class: str | None) -> dict[str, Any]:
    if not (strategy_module and strategy_config_class):
        return {}
    try:
        import importlib

        mod = importlib.import_module(strategy_module)
        cfg_cls = getattr(mod, strategy_config_class)
        inst = cfg_cls()
        full = inst.dict() if hasattr(inst, "dict") else {}
        allowed = set(getattr(cfg_cls, "__struct_fields__", ()))
        base = {
            "strategy_id",
            "order_id_tag",
            "use_uuid_client_order_ids",
            "use_hyphens_in_client_order_ids",
            "oms_type",
            "external_order_claims",
            "manage_contingent_orders",
            "manage_gtd_expiry",
            "manage_stop",
            "market_exit_interval_ms",
            "market_exit_max_attempts",
            "market_exit_time_in_force",
            "market_exit_reduce_only",
            "log_events",
            "log_commands",
            "log_rejected_due_post_only_as_warning",
        }
        return {k: v for k, v in full.items() if k in allowed and k not in base}
    except Exception:
        return {}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--initial-capital-usdc", type=float, default=10_000.0)
    p.add_argument("--actor", default="agent:hl_eval")
    p.add_argument(
        "--min-trades-floor",
        type=int,
        default=30,
        help="Reject as insufficient_trades when n_trades below this. Default 30; lower it for short data windows during integration testing.",
    )
    p.add_argument("--db", type=Path, default=Path("research/experiments.db"))
    p.add_argument(
        "--no-transition",
        action="store_true",
        help="Record experiment but do not call lifecycle.transition()",
    )
    args = p.parse_args()

    from trading_lab.agent import budget, lifecycle

    ok, n, cap = budget.check("backtests", db_path=args.db)
    if not ok:
        print(json.dumps({"ok": False, "error": "budget_exhausted", "consumed": n, "cap": cap}))
        return 3

    h = lifecycle.get_hypothesis(args.slug, db_path=args.db)
    if not h:
        print(json.dumps({"ok": False, "error": "hypothesis_not_found", "slug": args.slug}))
        return 2

    try:
        fm = _read_hypothesis_frontmatter(args.slug)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "hypothesis_frontmatter_error", "detail": str(exc)}))
        return 2
    if str(fm.get("venue", "polymarket")).lower() != "hyperliquid":
        print(json.dumps({"ok": False, "error": "wrong_venue", "slug": args.slug, "venue": fm.get("venue", "polymarket")}))
        return 2

    import os
    import subprocess as _sp

    env = os.environ.copy()
    cmd = [
        sys.executable,
        "scripts/hl_backtest.py",
        "--hypothesis-slug",
        args.slug,
        "--start",
        args.start,
        "--end",
        args.end,
        "--initial-capital-usdc",
        str(args.initial_capital_usdc),
    ]
    proc = _sp.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    if proc.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "hl_backtest_subprocess_failed",
                    "rc": proc.returncode,
                    "stderr_tail": proc.stderr[-400:],
                }
            )
        )
        return 4

    try:
        summary = _last_json(proc.stdout)
        extracted = _extract_result_metrics(summary)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "invalid_hl_backtest_stdout", "detail": str(exc)}))
        return 4

    exp_id = lifecycle.record_experiment(
        slug=args.slug,
        params=_recorded_params(h.strategy_module, h.strategy_config_class) or {},
        data_start=args.start,
        data_end=args.end,
        sharpe=float(extracted["sharpe"]),
        max_dd=float(extracted["max_dd_pct"]),
        fill_rate=float(extracted["fill_rate"]),
        pnl=float(extracted["pnl_usdc"]),
        n_trades=int(extracted["n_trades"]),
        db_path=args.db,
    )
    budget.consume("backtests", db_path=args.db)

    new_state, category = decide(
        float(extracted["sharpe"]),
        float(extracted["max_dd_pct"]),
        int(extracted["n_trades"]),
        pnl=float(extracted["pnl_usdc"]),
        min_trades=args.min_trades_floor,
    )

    out = {
        "ok": True,
        "venue": "hyperliquid",
        "experiment_id": exp_id,
        "slug": args.slug,
        "sharpe": extracted["sharpe"],
        "pnl_usdc": extracted["pnl_usdc"],
        "max_dd_pct": extracted["max_dd_pct"],
        "n_trades": extracted["n_trades"],
        "n_orders": extracted["n_orders"],
        "n_fills": extracted["n_fills"],
        "n_markets": extracted["n_markets"],
        "n_markets_with_fills": extracted["n_markets_with_fills"],
        "decision_new_state": new_state,
        "decision_rejection_category": category,
        "applied": False,
    }
    if not args.no_transition and h.state != new_state:
        try:
            lifecycle.transition(
                slug=args.slug,
                to_state=new_state,
                reason=(
                    f"hl_eval: sharpe={extracted['sharpe']:.3f} "
                    f"dd={extracted['max_dd_pct']:.1f}% trades={extracted['n_trades']} "
                    f"markets={extracted['n_markets_with_fills']}/{extracted['n_markets']}"
                ),
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
