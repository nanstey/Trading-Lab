from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hermes_remaining_crons = _load_script_module(
    "hermes_remaining_crons", "scripts/hermes_remaining_crons.py"
)
hermes_cron_auditor = _load_script_module(
    "hermes_cron_auditor", "scripts/hermes_cron_auditor.py"
)


def test_last_json_parses_pretty_printed_object() -> None:
    stdout = '{\n  "llm_tokens": 0,\n  "backtests": 0,\n  "paper_starts": 0,\n  "live_starts": 0\n}\n'
    parsed = hermes_remaining_crons._last_json(stdout)
    assert parsed["backtests"] == 0
    assert parsed["paper_starts"] == 0



def test_last_json_parses_final_multiline_object_after_logs() -> None:
    stdout = 'Running environment checks...\nstatus: ok\n{\n  "ok": true,\n  "decision_new_state": "BACKTEST"\n}\n'
    parsed = hermes_remaining_crons._last_json(stdout)
    assert parsed["ok"] is True
    assert parsed["decision_new_state"] == "BACKTEST"



def test_extract_response_reads_agent_response_section() -> None:
    text = '# Cron Job\n\n## Response\ncron-auditor: all clear\n'
    assert hermes_cron_auditor._extract_response(text) == 'cron-auditor: all clear'



def test_extract_response_reads_no_agent_stdout() -> None:
    text = (
        '# Cron Job: trading-lab-paper-watcher\n\n'
        '**Status:** script failed\n\n'
        'Script exited with code 1\n'
        'stdout:\n'
        'paper-watcher: runner missing (tick-mean-revert)\n'
    )
    assert (
        hermes_cron_auditor._extract_response(text)
        == 'paper-watcher: runner missing (tick-mean-revert)'
    )


def test_research_test_queue_dispatches_hl_eval(monkeypatch) -> None:
    monkeypatch.setattr(hermes_remaining_crons, "_budget_backtests", lambda: 0)
    monkeypatch.setattr(hermes_remaining_crons, "_oldest_slug", lambda states: "hl-bollinger-mr")
    monkeypatch.setattr(hermes_remaining_crons, "_slug_venue", lambda slug: "hyperliquid")
    monkeypatch.setattr(hermes_remaining_crons, "_slug_state", lambda slug: "OPTIMIZE")

    calls = []

    def fake_run_with_retry(cmd, *, timeout, retries=1):
        calls.append((cmd, timeout, retries))
        return 0, {"ok": True, "decision_new_state": "OPTIMIZE", "pnl_usdc": 12.5, "n_trades": 45}, ""

    monkeypatch.setattr(hermes_remaining_crons, "_run_with_retry", fake_run_with_retry)
    monkeypatch.setattr(hermes_remaining_crons, "_run", lambda *args, **kwargs: None)

    rc = hermes_remaining_crons.cron_research_test_queue()
    assert rc == 0
    assert calls
    assert calls[0][0][:2] == [".venv/bin/python3", "scripts/hl_eval_strategy.py"]
    assert "--slug" in calls[0][0]
    assert "hl-bollinger-mr" in calls[0][0]



def test_autocommit_new_files_runs_commit_helper(monkeypatch) -> None:
    before = {"research/paper_reports/existing.md"}
    after = before | {"research/paper_reports/new.md"}

    monkeypatch.setattr(hermes_remaining_crons, "_snapshot_files", lambda paths: after)
    monkeypatch.setattr(
        hermes_remaining_crons,
        "_run_json",
        lambda cmd, timeout=300: (0, {"ok": True, "commit": "abcdef123456"}, ""),
    )

    ok, detail = hermes_remaining_crons._autocommit_new_files(
        before=before,
        paths=["research/paper_reports"],
        message="chore(reports): record new paper summary artifacts",
    )

    assert ok is True
    assert detail == "abcdef123456"



def test_autocommit_new_files_noops_without_new_files(monkeypatch) -> None:
    before = {"research/paper_reports/existing.md"}
    monkeypatch.setattr(hermes_remaining_crons, "_snapshot_files", lambda paths: before)

    ok, detail = hermes_remaining_crons._autocommit_new_files(
        before=before,
        paths=["research/paper_reports"],
        message="chore(reports): record new paper summary artifacts",
    )

    assert ok is True
    assert detail is None



def test_optimizer_headline_prefers_best_summary() -> None:
    got = hermes_remaining_crons._optimizer_headline(
        {
            "decision_new_state": "PAPER_READY",
            "best_summary": {
                "recent_oos_pnl": 12.5,
                "methodology_score": 3456789.0,
            },
            "best_recent_oos_pnl": 3.0,
        }
    )
    assert got["state"] == "PAPER_READY"
    assert got["edge_value"] == 12.5
    assert got["methodology_score"] == 3456789.0



def test_optimizer_headline_falls_back_to_legacy_fields() -> None:
    got = hermes_remaining_crons._optimizer_headline(
        {
            "decision_state": "SHELVED",
            "decision_reason": "marginal_oos",
            "best_oos_total_pnl": 44.0,
        }
    )
    assert got["state"] == "SHELVED"
    assert got["category"] == "marginal_oos"
    assert got["edge_value"] == 44.0
    assert got["methodology_score"] is None



def test_research_optimize_queue_applies_hl_transition_before_verifying_state(monkeypatch, capsys) -> None:
    current_state = {"value": "OPTIMIZE"}
    transition_calls = []

    monkeypatch.setattr(hermes_remaining_crons, "_budget_backtests", lambda: 0)
    monkeypatch.setattr(hermes_remaining_crons, "_oldest_slug", lambda states: "hl-donchian")
    monkeypatch.setattr(hermes_remaining_crons, "_slug_venue", lambda slug: "hyperliquid")
    monkeypatch.setattr(hermes_remaining_crons, "_slug_state", lambda slug: current_state["value"])
    monkeypatch.setattr(hermes_remaining_crons, "_snapshot_files", lambda paths: set())
    monkeypatch.setattr(hermes_remaining_crons, "_autocommit_new_files", lambda **kwargs: (True, None))
    monkeypatch.setattr(hermes_remaining_crons, "_run", lambda *args, **kwargs: None)

    def fake_run_with_retry(cmd, *, timeout, retries=1):
        return (
            0,
            {
                "ok": True,
                "decision_new_state": "REJECTED",
                "decision_reason": "too_few_trades (min=0)",
                "best_oos_total_pnl": 1300.04,
            },
            "",
        )

    def fake_transition(slug, to_state, reason, *, actor):
        transition_calls.append((slug, to_state, reason, actor))
        current_state["value"] = to_state
        return True, "ok"

    monkeypatch.setattr(hermes_remaining_crons, "_run_with_retry", fake_run_with_retry)
    monkeypatch.setattr(hermes_remaining_crons, "_transition_slug", fake_transition)

    rc = hermes_remaining_crons.cron_research_optimize_queue()

    assert rc == 0
    assert transition_calls == [
        (
            "hl-donchian",
            "REJECTED",
            "hl_optimize: too_few_trades (min=0)",
            "agent:research-optimize-queue",
        )
    ]
    assert current_state["value"] == "REJECTED"
    assert "optimize-queue: hl-donchian -> REJECTED edge=1300.04" in capsys.readouterr().out
