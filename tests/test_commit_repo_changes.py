from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "commit_repo_changes.py"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    (repo / ".gitignore").write_text("ignored/\n*.parquet\n")
    (repo / "README.md").write_text("seed\n")
    _git(["add", ".gitignore", "README.md"], repo)
    _git(["commit", "-m", "init"], repo)
    return repo


def test_commit_repo_changes_noop(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--paths", "README.md", "--message", "noop test"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "noop"


def test_commit_repo_changes_force_adds_ignored_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "ignored" / "data.parquet"
    target.parent.mkdir()
    target.write_text("payload\n")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--paths",
            "ignored",
            "--message",
            "add ignored artifact",
            "--force",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "committed"
    head = _git(["log", "--oneline", "-n", "1"], repo).stdout
    assert "add ignored artifact" in head
    tracked = _git(["ls-files", "ignored/data.parquet"], repo).stdout.strip()
    assert tracked == "ignored/data.parquet"
