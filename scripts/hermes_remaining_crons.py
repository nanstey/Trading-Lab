#!/usr/bin/env python3
"""Deterministic Hermes cron wrappers for Trading-Lab jobs."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "research/experiments.db"
INBOX_DIR = REPO_ROOT / "research/manual_inbox"
LINK_DROPBOX_DIR = REPO_ROOT / "research/link_dropbox"
LOG_DIR = REPO_ROOT / "logs"
KILL_SWITCH_PATH = REPO_ROOT / "data/.kill_switch"


def _run(cmd: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=os.environ.copy(),
    )


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

    for line in reversed([line.strip() for line in stdout.splitlines()]):
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no_json_output")


def _detail(text: str) -> str:
    for line in reversed([line.strip() for line in text.splitlines()]):
        if line:
            return line[:180]
    return "no_detail"


def _run_json(cmd: list[str], *, timeout: int = 300) -> tuple[int, dict[str, Any] | None, str]:
    proc = _run(cmd, timeout=timeout)
    combined = f"{proc.stdout}\n{proc.stderr}"
    payload = None
    try:
        payload = _last_json(proc.stdout)
    except Exception:
        payload = None
    return proc.returncode, payload, combined


def _count_glob(dir_path: Path, pattern: str) -> int:
    return len(list(dir_path.glob(pattern))) if dir_path.exists() else 0


def _slug_state(slug: str) -> str | None:
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT state FROM hypotheses WHERE slug=?", (slug,)).fetchone()
    return row[0] if row else None


def _slug_venue(slug: str) -> str | None:
    md_path = REPO_ROOT / "research/hypotheses" / f"{slug}.md"
    if md_path.exists():
        text = md_path.read_text()
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                try:
                    import yaml

                    fm = yaml.safe_load(text[3:end].strip()) or {}
                    venue = fm.get("venue")
                    if venue:
                        return str(venue).lower()
                except Exception:
                    pass
    return None


def _slugs_in_state(state: str) -> list[str]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT slug FROM hypotheses WHERE state=? ORDER BY created_at ASC, slug ASC", (state,)
        ).fetchall()
    return [row[0] for row in rows]


def _oldest_slug(states: list[str]) -> str | None:
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        for state in states:
            row = conn.execute(
                "SELECT slug FROM hypotheses WHERE state=? ORDER BY created_at ASC, slug ASC LIMIT 1",
                (state,),
            ).fetchone()
            if row:
                return row[0]
    return None


def _budget_backtests() -> int:
    rc, payload, out = _run_json([".venv/bin/python3", "scripts/research_cli.py", "budget"], timeout=120)
    if rc != 0 or payload is None:
        raise RuntimeError(_detail(out))
    return int(payload.get("backtests", 0))


def _runner_active(slug: str) -> bool:
    proc = subprocess.run(["pgrep", "-af", f"paper_run_v2.py --slug {slug}"], text=True, capture_output=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _run_with_retry(cmd: list[str], *, timeout: int, retries: int = 1) -> tuple[int, dict[str, Any] | None, str]:
    last = (1, None, "")
    for _ in range(retries + 1):
        last = _run_json(cmd, timeout=timeout)
        rc, payload, combined = last
        if rc == 0 and payload is not None and payload.get("ok", True):
            return last
        lower = combined.lower()
        if not any(tok in lower for tok in ("timeout", "connection", "tempor", "api", "rate limit", "network", "502", "503", "504", "429", "retry")):
            return last
    return last


def cron_link_dropbox() -> int:
    _run([".venv/bin/python3", "scripts/research_cli.py", "init"], timeout=120)
    rc, payload, combined = _run_json(["make", "research-link-dropbox"], timeout=900)
    if payload is None:
        print(f"link-dropbox: failed ({_detail(combined)})")
        return 1
    processed = int(payload.get("processed_files", 0))
    error_files = int(payload.get("error_files", 0))
    captured = int(payload.get("captured", 0))
    pending_written = int(payload.get("pending_written", 0))
    duplicates = int(payload.get("duplicates", 0))
    if processed == 0 and error_files == 0 and captured == 0:
        return 0
    inbox_count = _count_glob(INBOX_DIR, "*.md")
    if pending_written and inbox_count < pending_written:
        print("link-dropbox: verify failed (pending inbox count low)")
        return 1
    if rc != 0 or error_files:
        print(f"link-dropbox: {error_files} errored")
        return 1 if rc != 0 else 0
    print(f"link-dropbox: {processed} processed, {pending_written} pending, {duplicates} duplicates")
    return 0


def cron_research_capture() -> int:
    _run([".venv/bin/python3", "scripts/research_cli.py", "init"], timeout=120)
    rc, payload, combined = _run_json(["make", "research-capture", "SOURCE_ARGS=--all --max-per-source 3"], timeout=1800)
    if payload is None:
        print(f"research-capture: failed ({_detail(combined)})")
        return 1
    captured = int(payload.get("captured", 0))
    pending_written = int(payload.get("pending_written", 0))
    duplicates = int(payload.get("duplicates", 0))
    errors = payload.get("errors", []) or []
    if captured == 0 and pending_written == 0 and not errors:
        return 0
    inbox_count = _count_glob(INBOX_DIR, "*.md")
    if pending_written and inbox_count < pending_written:
        print("research-capture: verify failed (pending inbox count low)")
        return 1
    if rc != 0 or errors:
        print(f"research-capture: {len(errors)} error(s)")
        return 1 if rc != 0 else 0
    print(f"research-capture: {captured} captured, {pending_written} pending, {duplicates} duplicates")
    return 0


def cron_research_discover() -> int:
    _run([".venv/bin/python3", "scripts/research_cli.py", "init"], timeout=120)
    if _count_glob(INBOX_DIR, "*.md") == 0:
        return 0
    rc, payload, combined = _run_json(["make", "research-discover"], timeout=1200)
    if payload is None:
        print(f"discover: failed ({_detail(combined)})")
        return 1
    discovered = int(payload.get("discovered", 0))
    errors = payload.get("errors", []) or []
    details = payload.get("details", []) or []
    slugs = [d.get("thesis_slug") or d.get("slug") for d in details if not d.get("error")]
    slugs = [s for s in slugs if s]
    verified = [s for s in slugs if _slug_state(s) == "PROPOSED"]
    if discovered == 0 and not errors:
        return 0
    if discovered and len(verified) != discovered:
        print("discover: verify failed")
        return 1
    if rc != 0 or errors:
        first = errors[0] if errors else {}
        name = first.get("spec_path") or first.get("thesis_slug") or first.get("error") or _detail(combined)
        print(f"discover: {len(errors)} error ({name})")
        return 1 if rc != 0 else 0
    shown = ", ".join(verified[:3])
    print(f"discover: {discovered} new ({shown})")
    return 0


def _queue_dates() -> tuple[str, str]:
    now = datetime.now(tz=UTC)
    return (now - timedelta(days=30)).date().isoformat(), now.date().isoformat()


def cron_research_test_queue() -> int:
    _run([".venv/bin/python3", "scripts/research_cli.py", "init"], timeout=120)
    try:
        if _budget_backtests() >= 50:
            print("test-queue: budget exhausted")
            return 0
    except Exception as exc:
        print(f"test-queue: failed ({exc})")
        return 1
    slug = _oldest_slug(["SMOKE_PASS", "BACKTEST"])
    if not slug:
        return 0
    venue = _slug_venue(slug) or "polymarket"
    start, end = _queue_dates()
    if venue == "hyperliquid":
        rc, payload, combined = _run_with_retry(
            [
                ".venv/bin/python3",
                "scripts/hl_eval_strategy.py",
                "--slug",
                slug,
                "--start",
                start,
                "--end",
                end,
            ],
            timeout=1800,
        )
    elif venue == "polymarket":
        rc, payload, combined = _run_with_retry(
            ["make", "research-test", f"SLUG={slug}", f"START={start}", f"END={end}"],
            timeout=1800,
        )
    else:
        print(f"test-queue: skipped unsupported venue for eval ({slug}: {venue})")
        return 0
    if payload is None:
        print(f"test-queue: {slug} failed ({_detail(combined)})")
        return 1
    new_state = payload.get("decision_new_state")
    if new_state and _slug_state(slug) != new_state:
        print(f"test-queue: {slug} failed (state verification mismatch)")
        return 1
    if rc != 0 or not payload.get("ok", False):
        reason = payload.get("error") or _detail(combined)
        print(f"test-queue: {slug} failed ({reason})")
        return 1
    pnl = float(payload.get("pnl_usdc", 0.0))
    trades = int(payload.get("n_trades", 0))
    print(f"test-queue: {slug} -> {new_state} pnl={pnl:.2f} trades={trades}")
    return 0


def cron_research_optimize_queue() -> int:
    _run([".venv/bin/python3", "scripts/research_cli.py", "init"], timeout=120)
    try:
        if _budget_backtests() >= 50:
            print("optimize-queue: budget exhausted")
            return 0
    except Exception as exc:
        print(f"optimize-queue: failed ({exc})")
        return 1
    slug = _oldest_slug(["OPTIMIZE"])
    if not slug:
        return 0
    venue = _slug_venue(slug) or "polymarket"
    start, end = _queue_dates()
    if venue == "hyperliquid":
        rc, payload, combined = _run_with_retry(
            [
                ".venv/bin/python3",
                "scripts/hl_optimize.py",
                "--slug",
                slug,
                "--data-start",
                start,
                "--data-end",
                end,
            ],
            timeout=3600,
        )
    else:
        rc, payload, combined = _run_with_retry(["make", "research-optimize", f"SLUG={slug}", f"START={start}", f"END={end}"], timeout=3600)
    if payload is None:
        print(f"optimize-queue: {slug} failed ({_detail(combined)})")
        return 1
    if payload.get("warning") == "grid_metrics_identical":
        print(f"optimize-queue: {slug} failed (grid_metrics_identical)")
        return 1
    new_state = payload.get("decision_new_state") or payload.get("decision_state")
    if new_state and _slug_state(slug) != new_state:
        print(f"optimize-queue: {slug} failed (state verification mismatch)")
        return 1
    if rc != 0 or not payload.get("ok", False):
        reason = payload.get("error") or _detail(combined)
        print(f"optimize-queue: {slug} failed ({reason})")
        return 1
    if "grid_metrics_identical" in (payload.get("warnings") or []) or payload.get("decision_rejection_category") == "param_space_inert":
        print(f"optimize-queue: {slug} failed (param_space_inert)")
        return 1
    recent_oos = float(
        payload.get(
            "best_recent_oos_pnl",
            payload.get("best_oos_total_pnl", payload.get("best_recent_oos_sharpe", payload.get("best_oos_mean_sharpe", 0.0))),
        )
    )
    print(f"optimize-queue: {slug} -> {new_state} recent_oos={recent_oos:.2f}")
    return 0


def cron_paper_summary() -> int:
    slugs = _slugs_in_state("PAPER")
    if not slugs:
        return 0
    utcdate = datetime.now(tz=UTC).strftime("%Y%m%d")
    failures: list[str] = []
    success_count = 0
    for slug in slugs:
        log_path = LOG_DIR / f"paper_{slug}_{utcdate}.jsonl"
        if not log_path.exists():
            if _runner_active(slug):
                continue
            failures.append(f"{slug}: runner_missing")
            continue
        rc, payload, combined = _run_with_retry([".venv/bin/python3", "scripts/paper_summary.py", "--slug", slug], timeout=600)
        if payload is None:
            failures.append(f"{slug}: {_detail(combined)}")
            continue
        if rc != 0 or not payload.get("ok", False):
            failures.append(f"{slug}: {payload.get('error') or _detail(combined)}")
            continue
        success_count += 1
    if not failures:
        return 0
    if len(failures) == len(slugs):
        first = "; ".join(failures[:2])
        print(f"paper-summary: {len(failures)} failed ({first})")
        return 1
    first = "; ".join(failures[:2])
    print(f"paper-summary: {len(failures)} failed ({first})")
    return 1


def cron_paper_watcher() -> int:
    slugs = _slugs_in_state("PAPER")
    if not slugs and not KILL_SWITCH_PATH.exists():
        return 0
    for slug in slugs:
        if not _runner_active(slug):
            print(f"paper-watcher: runner missing ({slug})")
            return 1
    rc, payload, combined = _run_json(["make", "paper-watcher"], timeout=600)
    if payload is None:
        print(f"paper-watcher: failed ({_detail(combined)})")
        return 1
    halted = payload.get("halted", []) or []
    retired = payload.get("retired", []) or []
    if KILL_SWITCH_PATH.exists() and not halted and not retired:
        print("paper-watcher: kill switch present")
        return 0
    if not halted and not retired:
        return 0
    if halted:
        first = halted[0].get("slug", "unknown")
        print(f"paper-watcher: halted {first}")
        return 0
    first = retired[0].get("slug", "unknown")
    print(f"paper-watcher: retired {first}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "job",
        choices=[
            "link-dropbox",
            "research-capture",
            "research-discover",
            "research-test-queue",
            "research-optimize-queue",
            "paper-summary",
            "paper-watcher",
        ],
    )
    args = p.parse_args()
    handlers = {
        "link-dropbox": cron_link_dropbox,
        "research-capture": cron_research_capture,
        "research-discover": cron_research_discover,
        "research-test-queue": cron_research_test_queue,
        "research-optimize-queue": cron_research_optimize_queue,
        "paper-summary": cron_paper_summary,
        "paper-watcher": cron_paper_watcher,
    }
    return handlers[args.job]()


if __name__ == "__main__":
    sys.exit(main())
