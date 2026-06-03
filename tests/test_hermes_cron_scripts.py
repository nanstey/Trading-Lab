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
