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


paper_summary = _load_script_module("paper_summary", "scripts/paper_summary.py")


def test_render_report_handles_empty_logs() -> None:
    report = paper_summary._render_report(
        slug="tick-mean-revert",
        date="20260605",
        first_ts=None,
        last_ts=None,
        n_signals=0,
        pairs=[],
        unmatched=[],
        per_token={},
        total_pnl=0.0,
    )
    assert "_no signals captured during window_" in report
    assert "| _none_ | 0 | $0.0000 |" in report


def test_record_into_db_skips_empty_pairs(tmp_path: Path) -> None:
    db_path = tmp_path / "experiments.db"
    paper_summary._record_into_db(
        slug="tick-mean-revert",
        date="20260605",
        pairs=[],
        total_pnl=0.0,
        db_path=db_path,
    )
    assert not db_path.exists()