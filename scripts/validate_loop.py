#!/usr/bin/env python3
"""
Phase 5.11 end-to-end loop validation.

Drives a known-bad and a known-good hypothesis through the full agentic
loop without sub-agent calls (uses the existing scripts directly), asserts
the decision rules behave correctly, and prints a JSON verdict.

Known-bad fixture
-----------------
A hypothesis that DELIBERATELY violates the codegen import allowlist (or
uses a lookahead-flagged name). The smoke step must REJECT it; we assert
state ends at REJECTED with `rejection_category` in
{import_violation, lookahead_suspected, syntax_error}.

Known-good fixture
------------------
The existing `tick-mean-revert` strategy (or any strategy already in
SMOKE_PASS+ that produces positive PnL on our data window). Drives it
through eval + optimize; asserts state reaches OPTIMIZE or PAPER_READY.

Outcome JSON
------------
  {"ok": true, "known_bad": {"final_state": "REJECTED", ...},
                 "known_good": {"final_state": "OPTIMIZE", ...}}

Non-zero exit on any assertion failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

KNOWN_BAD_SLUG = "loop-validation-bad"
KNOWN_BAD_STRATEGY_PATH = "src/nautilus_predict/strategies/loop_validation_bad.py"
KNOWN_BAD_TEST_PATH = "tests/strategies/test_loop_validation_bad.py"
KNOWN_BAD_HYP_PATH = "research/hypotheses/loop-validation-bad.md"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--known-good-slug", default="tick-mean-revert",
        help="Pre-existing strategy slug expected to pass eval (default: tick-mean-revert)",
    )
    p.add_argument(
        "--skip-known-good", action="store_true",
        help="Only validate the known-bad rejection path",
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    args = p.parse_args()

    results: dict = {"ok": True, "checks": []}

    bad = _validate_known_bad(args.db)
    results["known_bad"] = bad
    if not bad["pass"]:
        results["ok"] = False
        results["checks"].append("known_bad_failed")

    if not args.skip_known_good:
        good = _validate_known_good(args.known_good_slug, args.db)
        results["known_good"] = good
        if not good["pass"]:
            results["ok"] = False
            results["checks"].append("known_good_failed")

    print(json.dumps(results))
    return 0 if results["ok"] else 1


# ---------------------------------------------------------------------------
# Known-bad path
# ---------------------------------------------------------------------------


def _validate_known_bad(db_path: Path) -> dict:
    """Write a deliberately-illegal strategy, run smoke, assert REJECTED."""

    # Always start from a clean slate for this slug.
    _delete_files()
    _delete_hypothesis_row(KNOWN_BAD_SLUG, db_path)

    # 1. Hypothesis MD with strategy refs.
    Path(KNOWN_BAD_HYP_PATH).write_text(
        "---\n"
        f"slug: {KNOWN_BAD_SLUG}\n"
        "source: validation\n"
        "source_url: null\n"
        "state: PROPOSED\n"
        "market_criteria:\n"
        "  outcome_type: binary\n"
        "  count: 1\n"
        f"strategy_module: nautilus_predict.strategies.loop_validation_bad\n"
        "strategy_class: LoopValidationBadStrategy\n"
        "strategy_config_class: LoopValidationBadConfig\n"
        "---\n\n"
        "# loop-validation-bad\n\n"
        "Deliberately-illegal strategy (imports `requests` + has\n"
        "`_future_price` attribute) — used by scripts/validate_loop.py to\n"
        "assert the smoke guards reject it.\n"
    )

    # 2. Strategy file that breaks BOTH import allowlist AND lookahead heuristic.
    Path(KNOWN_BAD_STRATEGY_PATH).write_text(
        '"""Deliberately illegal — for loop validation only."""\n'
        "from __future__ import annotations\n"
        "import requests  # not in allowlist\n"
        "from nautilus_trader.config import StrategyConfig\n"
        "from nautilus_trader.trading.strategy import Strategy\n"
        "\n"
        "class LoopValidationBadConfig(StrategyConfig, frozen=True):\n"
        '    strategy_id: str = "LOOP-VALIDATION-BAD-001"\n'
        "\n"
        "class LoopValidationBadStrategy(Strategy):\n"
        "    def __init__(self, config):\n"
        "        super().__init__(config)\n"
        "        self._future_price = 0.0  # lookahead-flagged attr\n"
        "    def on_start(self): pass\n"
        "    def on_stop(self): pass\n"
    )
    # 3. Stub test file (smoke runs pytest if it exists).
    Path(KNOWN_BAD_TEST_PATH).write_text(
        "def test_dummy(): assert True\n"
    )

    # 4. Register hypothesis.
    rc = _run_script("propose_hypothesis.py", ["--file", KNOWN_BAD_HYP_PATH])
    if rc != 0:
        return {"pass": False, "step": "propose_hypothesis", "rc": rc}

    # 5. Transition PROPOSED → CODEGEN (matches codegen-strategy.md flow).
    rc = _run_script(
        "transition_lifecycle.py",
        ["--slug", KNOWN_BAD_SLUG, "--to", "CODEGEN",
         "--reason", "validate_loop: smoke test", "--actor", "agent:codegen"],
    )
    if rc != 0:
        return {"pass": False, "step": "transition_codegen", "rc": rc}

    # 6. Run smoke — expect rejection JSON.
    smoke = _run_script_capture("smoke_test_strategy.py", ["--slug", KNOWN_BAD_SLUG])
    smoke_json = _last_json(smoke.stdout)
    if smoke_json is None or smoke_json.get("ok") is not False:
        return {"pass": False, "step": "smoke", "stdout_tail": smoke.stdout[-400:]}
    rejection_category = smoke_json.get("rejection_category")

    # 7. Transition to REJECTED with the category smoke surfaced.
    rc = _run_script(
        "transition_lifecycle.py",
        ["--slug", KNOWN_BAD_SLUG, "--to", "REJECTED",
         "--reason", f"smoke rejected: {rejection_category}",
         "--rejection-category", rejection_category or "smoke_crash",
         "--actor", "agent:codegen"],
    )
    if rc != 0:
        return {"pass": False, "step": "transition_rejected", "rc": rc}

    h = lifecycle_get(KNOWN_BAD_SLUG, db_path)
    final_state = h.get("state") if h else None
    passed = (
        final_state == "REJECTED"
        and rejection_category in {"import_violation", "lookahead_suspected", "syntax_error"}
    )

    # Cleanup the synthetic files so the codebase isn't polluted between runs.
    _delete_files()

    return {
        "pass": passed,
        "final_state": final_state,
        "rejection_category": rejection_category,
        "smoke_violations": smoke_json.get("violations", []),
    }


# ---------------------------------------------------------------------------
# Known-good path
# ---------------------------------------------------------------------------


def _validate_known_good(slug: str, db_path: Path) -> dict:
    """
    Re-eval an existing strategy; assert PnL>0 and state passes.

    To avoid demoting a slug that's already in PAPER (or further), only
    actually run eval when the slug is in SMOKE_PASS / BACKTEST. For any
    advanced state, look up the most-recent eval-style experiment and
    apply the same pass criteria.
    """
    from nautilus_predict.agent import lifecycle

    h = lifecycle.get_hypothesis(slug, db_path=db_path)
    if not h:
        return {"pass": False, "error": "hypothesis_not_found", "slug": slug}

    advanced_states = {"OPTIMIZE", "PAPER_READY", "PAPER", "LIVE_READY", "LIVE"}
    if h.state in advanced_states:
        # Strategy already past BACKTEST — verify it has a positive-PnL eval
        # row on record, without re-running (which would demote it).
        for row in lifecycle.list_experiments(slug, db_path=db_path, limit=100):
            try:
                params = json.loads(row.get("params_json") or "{}")
            except Exception:
                params = {}
            if "_paper_summary" in params or "_wf_window" in params:
                continue
            pnl = float(row.get("pnl") or 0.0)
            n_trades = int(row.get("n_trades") or 0)
            if pnl > 0 and n_trades >= 30:
                return {
                    "pass": True,
                    "final_state": h.state,
                    "pnl_usdc": pnl,
                    "n_trades": n_trades,
                    "experiment_id": row.get("id"),
                    "note": "verified via existing experiments row — no re-eval",
                }
        return {
            "pass": False, "final_state": h.state,
            "reason": "no positive-PnL eval row on record",
        }

    # Otherwise re-eval and let the decision rule transition.
    eval_out = _run_script_capture(
        "eval_strategy.py",
        ["--slug", slug, "--start", "2026-05-24", "--end", "2026-05-26"],
    )
    eval_json = _last_json(eval_out.stdout) or {}
    if not eval_json.get("ok"):
        return {
            "pass": False, "step": "eval",
            "stdout_tail": eval_out.stdout[-400:],
            "stderr_tail": eval_out.stderr[-200:],
        }

    h2 = lifecycle.get_hypothesis(slug, db_path=db_path)
    final_state = h2.state if h2 else None
    pnl = eval_json.get("pnl_usdc", 0.0)
    n_trades = eval_json.get("n_trades", 0)
    passed = (
        final_state in ("OPTIMIZE", "PAPER_READY", "PAPER")
        and pnl > 0
        and n_trades >= 30
    )
    return {
        "pass": passed,
        "final_state": final_state,
        "pnl_usdc": pnl,
        "n_trades": n_trades,
        "experiment_id": eval_json.get("experiment_id"),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delete_files() -> None:
    for path in (
        KNOWN_BAD_STRATEGY_PATH,
        KNOWN_BAD_TEST_PATH,
        KNOWN_BAD_HYP_PATH,
    ):
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def _delete_hypothesis_row(slug: str, db_path: Path) -> None:
    import sqlite3
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as c:
        c.execute("DELETE FROM hypotheses WHERE slug=?", (slug,))
        c.execute("DELETE FROM lifecycle_transitions WHERE slug=?", (slug,))
        c.execute("DELETE FROM experiments WHERE slug=?", (slug,))


def _run_script(name: str, args: list[str]) -> int:
    cmd = [sys.executable, f"scripts/{name}"] + args
    return subprocess.run(cmd, env=os.environ.copy()).returncode


def _run_script_capture(name: str, args: list[str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, f"scripts/{name}"] + args
    return subprocess.run(
        cmd, capture_output=True, text=True, env=os.environ.copy(), timeout=900,
    )


def _last_json(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def lifecycle_get(slug: str, db_path: Path) -> dict | None:
    from nautilus_predict.agent import lifecycle
    h = lifecycle.get_hypothesis(slug, db_path=db_path)
    if not h:
        return None
    return {"slug": h.slug, "state": h.state}


if __name__ == "__main__":
    sys.exit(main())
