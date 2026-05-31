"""Helpers for scheduled Hyperliquid daily catalog capture.

This module defines the deterministic planning logic behind the daily
capture wrapper:

- which intervals to pull by default
- how far back to overlap for each interval / funding stream
- how to choose the coin set for the run

The actual network I/O lives in `scripts/capture_hyperliquid_daily.py`.
Keeping the planning logic here makes it easy to unit-test without mocking
HTTP calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

DEFAULT_INTERVALS: tuple[str, ...] = ("5m", "1h", "1d")
DEFAULT_INTERVAL_LOOKBACK_DAYS: dict[str, int] = {
    # Small overlap for high-frequency bars; enough to heal occasional
    # missed cron runs without re-pulling the full 5000-row horizon.
    "5m": 7,
    # Hourly research currently relies on ~6-7 months of history. A 14-day
    # overlap is cheap and repairs short outages.
    "1h": 14,
    # Daily bars are sparse; a longer overlap is harmless and keeps the tail
    # robust if the job is paused for weeks.
    "1d": 120,
}
DEFAULT_FUNDING_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class CaptureWindow:
    interval: str
    start_date: str
    end_date: str
    lookback_days: int


@dataclass(frozen=True)
class DailyCapturePlan:
    as_of: str
    intervals: tuple[str, ...]
    windows: tuple[CaptureWindow, ...]
    funding_start_date: str
    funding_end_date: str
    funding_lookback_days: int


def normalize_intervals(intervals: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not intervals:
        return DEFAULT_INTERVALS
    out: list[str] = []
    seen: set[str] = set()
    for raw in intervals:
        interval = raw.strip()
        if not interval:
            continue
        if interval not in DEFAULT_INTERVAL_LOOKBACK_DAYS:
            raise ValueError(
                f"Unsupported interval {interval!r}; choose from {sorted(DEFAULT_INTERVAL_LOOKBACK_DAYS)}"
            )
        if interval in seen:
            continue
        seen.add(interval)
        out.append(interval)
    if not out:
        raise ValueError("No valid intervals provided")
    return tuple(out)


def build_daily_capture_plan(
    *,
    as_of: date,
    intervals: list[str] | tuple[str, ...] | None = None,
    interval_lookbacks: dict[str, int] | None = None,
    funding_lookback_days: int = DEFAULT_FUNDING_LOOKBACK_DAYS,
) -> DailyCapturePlan:
    """Return deterministic date windows for one incremental daily capture run.

    `end_date` is the capture day's date label and is passed through to the
    existing download script semantics. The underlying historical client treats
    the request window as open on the right edge, so a run with `as_of=2026-05-31`
    captures bars/funding strictly before 2026-05-31T00:00:00Z.
    """
    interval_names = normalize_intervals(intervals)
    lookbacks = dict(DEFAULT_INTERVAL_LOOKBACK_DAYS)
    if interval_lookbacks:
        lookbacks.update(interval_lookbacks)

    if funding_lookback_days < 1:
        raise ValueError("funding_lookback_days must be >= 1")

    end_date = as_of.isoformat()
    windows = tuple(
        CaptureWindow(
            interval=interval,
            start_date=(as_of - timedelta(days=lookbacks[interval])).isoformat(),
            end_date=end_date,
            lookback_days=lookbacks[interval],
        )
        for interval in interval_names
    )
    return DailyCapturePlan(
        as_of=end_date,
        intervals=interval_names,
        windows=windows,
        funding_start_date=(as_of - timedelta(days=funding_lookback_days)).isoformat(),
        funding_end_date=end_date,
        funding_lookback_days=funding_lookback_days,
    )


def choose_capture_coins(
    *,
    explicit: list[str] | None,
    latest_snapshot_coins: list[str],
    today_snapshot_coins: list[str],
) -> list[str]:
    """Resolve the coin set for a daily capture run.

    Preference order:
    1. explicit CLI list
    2. union(latest persisted snapshot, today's freshly-computed snapshot)

    The union avoids a brittle failure mode where a coin drops out of today's
    top-N snapshot and then stops receiving fresh bars/funding immediately.
    """
    source = explicit if explicit else [*latest_snapshot_coins, *today_snapshot_coins]
    out: list[str] = []
    seen: set[str] = set()
    for coin in source:
        name = coin.strip().upper()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def latest_snapshot_coins(
    snapshot_dates: list[str],
    reader: Callable[[str], object],
) -> list[str]:
    """Load coins from the latest persisted universe snapshot, if any."""
    if not snapshot_dates:
        return []
    latest = sorted(snapshot_dates)[-1]
    df = reader(latest)
    columns = getattr(df, "columns", [])
    is_empty = bool(getattr(df, "empty", True))
    if is_empty or "coin" not in columns:
        return []
    coin_col = cast(Any, df)["coin"]
    return [str(v).upper() for v in coin_col.tolist() if str(v).strip()]


__all__ = [
    "CaptureWindow",
    "DEFAULT_FUNDING_LOOKBACK_DAYS",
    "DEFAULT_INTERVALS",
    "DEFAULT_INTERVAL_LOOKBACK_DAYS",
    "DailyCapturePlan",
    "build_daily_capture_plan",
    "choose_capture_coins",
    "latest_snapshot_coins",
    "normalize_intervals",
]
