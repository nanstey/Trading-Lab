"""
Walk-forward window construction.

Two modes:
  * `anchored`  — train window starts at `data_start` and grows; test windows
                  slide forward. Standard for non-stationary series.
  * `rolling`   — train window has a fixed length and slides forward in
                  lockstep with the test window. Good when you suspect the
                  edge decays and you don't want stale training data.

Both modes support:
  * `embargo`   — purge a buffer of bars between train.end and test.start so
                  rolling indicators with a window > embargo can't leak
                  information across the boundary.
  * `min_train_days` / `min_test_days` — refuse to emit windows smaller than
                  these floors; backtest results below a sample-size threshold
                  are noise.

Output is a list of `WalkForwardWindow` records: train.start/end +
test.start/end as UTC datetimes plus a 0-indexed fold id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class WalkForwardWindow:
    fold: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    def as_dict(self) -> dict[str, str | int]:
        return {
            "fold": self.fold,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
        }


def make_walk_forward_windows(
    data_start: datetime,
    data_end: datetime,
    *,
    mode: str = "anchored",
    n_folds: int = 5,
    train_test_ratio: float = 3.0,
    embargo_days: int = 1,
    min_train_days: int = 30,
    min_test_days: int = 7,
) -> list[WalkForwardWindow]:
    """
    Generate `n_folds` walk-forward windows over `[data_start, data_end]`.

    Geometry (anchored mode, ratio=3, n_folds=5):
        |--- train_0 -------|x| test_0 |
        |--- train_1 -----------|x| test_1 |
        |--- train_2 ---------------|x| test_2 |
        ...
    `x` = embargo. Test windows tile end-to-end so every datapoint after the
    minimum train length is evaluated OOS exactly once.

    Geometry (rolling mode):
        | train_0 ----|x| test_0 |
                | train_1 ----|x| test_1 |
                       | train_2 ----|x| test_2 |
        ...
    """
    if data_end <= data_start:
        return []
    if n_folds < 1:
        return []
    if mode not in ("anchored", "rolling"):
        raise ValueError(f"mode must be 'anchored' or 'rolling'; got {mode!r}")

    embargo = timedelta(days=embargo_days)
    total_span = data_end - data_start
    total_days = total_span.days
    if total_days < (min_train_days + embargo_days + min_test_days):
        return []

    # Each fold gets a slice of size `step` for its test window.
    test_span_total = total_span - timedelta(days=min_train_days + embargo_days)
    test_window_days = max(int(test_span_total.days // n_folds), min_test_days)

    windows: list[WalkForwardWindow] = []

    for i in range(n_folds):
        test_start = data_start + timedelta(days=min_train_days + embargo_days + i * test_window_days)
        test_end = test_start + timedelta(days=test_window_days)
        if test_end > data_end:
            test_end = data_end
        if (test_end - test_start).days < min_test_days:
            break

        if mode == "anchored":
            train_start = data_start
            train_end = test_start - embargo
        else:  # rolling
            # Fixed-length train window of (ratio * test_window_days).
            train_days = max(int(train_test_ratio * test_window_days), min_train_days)
            train_end = test_start - embargo
            train_start = train_end - timedelta(days=train_days)
            if train_start < data_start:
                train_start = data_start

        if (train_end - train_start).days < min_train_days:
            continue

        windows.append(
            WalkForwardWindow(
                fold=i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )

    return windows


def coverage_summary(windows: list[WalkForwardWindow]) -> dict[str, object]:
    if not windows:
        return {"n_folds": 0}
    train_days = [(w.train_end - w.train_start).days for w in windows]
    test_days = [(w.test_end - w.test_start).days for w in windows]
    return {
        "n_folds": len(windows),
        "train_days_min": min(train_days),
        "train_days_max": max(train_days),
        "test_days_min": min(test_days),
        "test_days_max": max(test_days),
        "first_test_start": windows[0].test_start.isoformat(),
        "last_test_end": windows[-1].test_end.isoformat(),
    }


__all__ = [
    "WalkForwardWindow",
    "coverage_summary",
    "make_walk_forward_windows",
]
