#!/usr/bin/env python3
"""Proactive, low-token Trading-Lab cron auditor.

Designed for Hermes `no_agent=True` cron execution.

Behavior:
- parse only the `## Response` section of recent cron output markdown
- remediate safe, deterministic issues directly from code
- stay silent on healthy runs (print nothing)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
CRON_OUTPUT_ROOT = Path("/home/nautilus/.hermes/profiles/gambit/cron/output")
STATE_PATH = REPO_ROOT / "data/cron_auditor_state.json"
EXPERIMENTS_DB = REPO_ROOT / "research/experiments.db"
KILL_SWITCH_PATH = REPO_ROOT / "data/.kill_switch"
RUNNER_LOG_DIR = REPO_ROOT / "logs/cron_auditor"
PAPER_DURATION_SECS = 86400

SYNC_DAILY_JOB = "ad4d8cd0c6a6"
SYNC_FULL_JOB = "b89aa9e6dcb5"
PAPER_SUMMARY_JOB = "a14ddd31376d"
PAPER_WATCHER_JOB = "9c79ce027f5d"

SYNC_RECOVERY_STREAK_THRESHOLD = 3


@dataclass
class CronOutput:
    job_id: str
    path: Path | None
    response: str | None
    run_time: datetime | None


def _latest_output(job_id: str) -> CronOutput:
    job_dir = CRON_OUTPUT_ROOT / job_id
    if not job_dir.exists():
        return CronOutput(job_id=job_id, path=None, response=None, run_time=None)
    files = sorted(job_dir.glob("*.md"))
    if not files:
        return CronOutput(job_id=job_id, path=None, response=None, run_time=None)
    path = files[-1]
    text = path.read_text(errors="ignore")
    response = _extract_response(text)
    run_time = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return CronOutput(job_id=job_id, path=path, response=response, run_time=run_time)


def _extract_response(text: str) -> str | None:
    marker = "## Response"
    if marker not in text:
        return _extract_no_agent_response(text)
    tail = text.split(marker, 1)[1]
    for line in tail.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _extract_no_agent_response(text: str) -> str | None:
    stdout_marker = "stdout:"
    if stdout_marker not in text:
        return None
    tail = text.split(stdout_marker, 1)[1]
    for line in tail.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"actions": {}}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"actions": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _mark_action(state: dict, key: str, marker: str) -> bool:
    actions = state.setdefault("actions", {})
    if actions.get(key) == marker:
        return False
    actions[key] = marker
    return True


def _run(cmd: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=os.environ.copy(),
    )


def _detail(text: str) -> str:
    for line in reversed([line.strip() for line in text.splitlines()]):
        if line:
            return line[:180]
    return "no_detail"


def _is_transient_sync_failure(response: str | None) -> bool:
    if not response:
        return False
    lowered = response.lower()
    return (
        lowered.startswith("sync-markets")
        and "failed" in lowered
        and any(marker in lowered for marker in ("422", "gamma", "pagination", "unprocessable"))
    )


def _count_recent_recoveries(job_id: str, limit: int = 5) -> int:
    job_dir = CRON_OUTPUT_ROOT / job_id
    if not job_dir.exists():
        return 0
    count = 0
    for path in sorted(job_dir.glob("*.md"), reverse=True)[:limit]:
        response = _extract_response(path.read_text(errors="ignore")) or ""
        if "recovered after retry" in response:
            count += 1
        else:
            break
    return count


def _slug_state(slug: str) -> str | None:
    if not EXPERIMENTS_DB.exists():
        return None
    with sqlite3.connect(EXPERIMENTS_DB) as conn:
        row = conn.execute("SELECT state FROM hypotheses WHERE slug=?", (slug,)).fetchone()
    return row[0] if row else None


def _paper_slugs() -> list[str]:
    if not EXPERIMENTS_DB.exists():
        return []
    with sqlite3.connect(EXPERIMENTS_DB) as conn:
        rows = conn.execute(
            "SELECT slug FROM hypotheses WHERE state='PAPER' ORDER BY slug"
        ).fetchall()
    return [row[0] for row in rows]


def _runner_active(slug: str) -> bool:
    proc = subprocess.run(
        ["pgrep", "-af", f"paper_run_v2.py --slug {slug}"],
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _start_runner(slug: str) -> tuple[bool, str]:
    RUNNER_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RUNNER_LOG_DIR / f"paper_run_{slug}.log"
    with log_path.open("ab") as fh:
        proc = subprocess.Popen(
            [
                ".venv/bin/python3",
                "scripts/paper_run_v2.py",
                "--slug",
                slug,
                "--duration-secs",
                str(PAPER_DURATION_SECS),
            ],
            cwd=REPO_ROOT,
            stdout=fh,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            env=os.environ.copy(),
        )
    if proc.poll() is not None:
        return False, f"runner exited immediately ({proc.returncode})"
    return True, str(log_path)


def _run_paper_summary(slug: str) -> tuple[bool, str]:
    proc = _run([".venv/bin/python3", "scripts/paper_summary.py", "--slug", slug], timeout=240)
    if proc.returncode == 0:
        return True, _detail(proc.stdout)
    return False, _detail(f"{proc.stdout}\n{proc.stderr}")


def _run_paper_watcher() -> tuple[bool, str]:
    proc = _run(["make", "paper-watcher"], timeout=240)
    if proc.returncode == 0:
        return True, _detail(proc.stdout)
    return False, _detail(f"{proc.stdout}\n{proc.stderr}")


def _run_sync(full: bool) -> tuple[bool, str]:
    cmd = [".venv/bin/python3", "scripts/hermes_sync_markets_cron.py"]
    if full:
        cmd.append("--full")
    proc = _run(cmd, timeout=600)
    output = _detail(f"{proc.stdout}\n{proc.stderr}")
    return proc.returncode == 0, output


def _stale(output: CronOutput, *, minutes: int) -> bool:
    if output.run_time is None:
        return True
    return datetime.now(tz=UTC) - output.run_time > timedelta(minutes=minutes)


def _iter_runner_missing(responses: Iterable[tuple[str, CronOutput]]) -> Iterable[tuple[str, CronOutput, str]]:
    pat = re.compile(r"runner missing \(([^)]+)\)")
    for source, output in responses:
        resp = output.response or ""
        m = pat.search(resp)
        if m:
            yield source, output, m.group(1)


def main() -> int:
    state = _load_state()
    actions: list[str] = []
    now_marker = datetime.now(tz=UTC).isoformat()

    latest = {
        "paper_summary": _latest_output(PAPER_SUMMARY_JOB),
        "paper_watcher": _latest_output(PAPER_WATCHER_JOB),
        "sync_daily": _latest_output(SYNC_DAILY_JOB),
        "sync_full": _latest_output(SYNC_FULL_JOB),
    }

    if _stale(latest["paper_summary"], minutes=95):
        marker = f"paper_summary_stale:{latest['paper_summary'].path}"
        if _mark_action(state, "paper_summary_stale", marker):
            slugs = _paper_slugs()
            if slugs:
                failures = []
                for slug in slugs:
                    ok, detail = _run_paper_summary(slug)
                    if not ok:
                        failures.append(f"{slug}: {detail}")
                if failures:
                    actions.append(f"paper-summary stale; rerun partial failure ({'; '.join(failures[:2])})")
                else:
                    actions.append(f"paper-summary stale; reran {len(slugs)} PAPER slug(s)")

    if _stale(latest["paper_watcher"], minutes=95):
        marker = f"paper_watcher_stale:{latest['paper_watcher'].path}"
        if _mark_action(state, "paper_watcher_stale", marker):
            ok, detail = _run_paper_watcher()
            if ok:
                actions.append("paper-watcher stale; reran watcher")
            else:
                actions.append(f"paper-watcher stale; rerun failed ({detail})")

    runner_missing_sources = [
        ("paper-summary", latest["paper_summary"]),
        ("paper-watcher", latest["paper_watcher"]),
    ]
    if not KILL_SWITCH_PATH.exists():
        for source, output, slug in _iter_runner_missing(runner_missing_sources):
            marker = f"{source}:{output.path}:{slug}"
            if not _mark_action(state, f"runner_missing:{slug}", marker):
                continue
            if _slug_state(slug) != "PAPER":
                actions.append(f"runner-missing ignored; {slug} no longer PAPER")
                continue
            if _runner_active(slug):
                actions.append(f"runner-missing ignored; {slug} already active")
                continue
            ok, detail = _start_runner(slug)
            if not ok:
                actions.append(f"runner restart failed for {slug} ({detail})")
                continue
            summary_ok, summary_detail = _run_paper_summary(slug)
            if summary_ok:
                actions.append(f"restarted paper runner for {slug}; refreshed summary")
            else:
                actions.append(f"restarted paper runner for {slug}; summary refresh failed ({summary_detail})")
    else:
        for source, output, slug in _iter_runner_missing(runner_missing_sources):
            marker = f"{source}:{output.path}:{slug}:killswitch"
            _mark_action(state, f"runner_missing:{slug}", marker)

    for key, output, full in (
        ("sync_daily_failure", latest["sync_daily"], False),
        ("sync_full_failure", latest["sync_full"], True),
    ):
        if _is_transient_sync_failure(output.response):
            marker = f"{output.path}:{output.response}"
            if _mark_action(state, key, marker):
                ok, detail = _run_sync(full=full)
                scope = "sync-markets full" if full else "sync-markets daily"
                if ok:
                    actions.append(f"reran {scope} after transient failure")
                else:
                    actions.append(f"reran {scope} but it still failed ({detail})")

    for key, job_id, label in (
        ("sync_daily_recovery_streak", SYNC_DAILY_JOB, "sync-markets daily"),
        ("sync_full_recovery_streak", SYNC_FULL_JOB, "sync-markets full"),
    ):
        streak = _count_recent_recoveries(job_id)
        if streak >= SYNC_RECOVERY_STREAK_THRESHOLD:
            marker = f"{job_id}:recovery_streak:{streak}"
            if _mark_action(state, key, marker):
                actions.append(f"{label} has {streak} consecutive retry recoveries; consider making smaller page-size the default")

    _save_state(state)

    if actions:
        print("cron-auditor: " + "; ".join(actions[:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
