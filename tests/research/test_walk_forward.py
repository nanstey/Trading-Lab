"""Tests for `research.walk_forward`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_lab.research.walk_forward import (
    coverage_summary,
    make_walk_forward_windows,
)


def _dt(day_offset: int) -> datetime:
    return datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=day_offset)


def test_anchored_produces_requested_fold_count():
    windows = make_walk_forward_windows(
        _dt(0), _dt(365), mode="anchored", n_folds=5, embargo_days=1
    )
    assert len(windows) == 5
    # All train windows start at data_start.
    assert {w.train_start for w in windows} == {_dt(0)}
    # Test windows tile end-to-end without overlap.
    for i in range(1, len(windows)):
        assert windows[i].test_start == windows[i - 1].test_end


def test_anchored_train_grows_with_each_fold():
    windows = make_walk_forward_windows(
        _dt(0), _dt(365), mode="anchored", n_folds=4, embargo_days=2
    )
    train_lengths = [(w.train_end - w.train_start).days for w in windows]
    assert train_lengths == sorted(train_lengths)
    assert train_lengths[0] < train_lengths[-1]


def test_rolling_train_length_constant_after_warmup():
    # Use a longer data span so the requested train window (ratio * test) fits
    # cleanly for every fold without being clipped at data_start.
    windows = make_walk_forward_windows(
        _dt(0), _dt(900), mode="rolling", n_folds=4, embargo_days=1, train_test_ratio=2.0
    )
    assert len(windows) == 4
    train_lengths = [(w.train_end - w.train_start).days for w in windows]
    # Once the rolling window has fully warmed, it holds a fixed length.
    # Early folds may still be clipped by data_start.
    assert train_lengths[-1] == train_lengths[-2]


def test_embargo_is_respected():
    windows = make_walk_forward_windows(
        _dt(0), _dt(365), mode="anchored", n_folds=5, embargo_days=3
    )
    for w in windows:
        gap = (w.test_start - w.train_end).days
        assert gap == 3


def test_insufficient_data_returns_empty():
    # 10 days total, requires 30 train + 1 embargo + 7 test = 38.
    windows = make_walk_forward_windows(
        _dt(0), _dt(10), mode="anchored", n_folds=3
    )
    assert windows == []


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        make_walk_forward_windows(_dt(0), _dt(365), mode="nonsense")


def test_coverage_summary_keys():
    windows = make_walk_forward_windows(_dt(0), _dt(365), mode="anchored", n_folds=4)
    summary = coverage_summary(windows)
    assert summary["n_folds"] == len(windows)
    assert "train_days_min" in summary
    assert "test_days_max" in summary


def test_empty_window_summary():
    assert coverage_summary([]) == {"n_folds": 0}
