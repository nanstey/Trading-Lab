"""Tests for `research.overfitting`."""

from __future__ import annotations

import numpy as np

from trading_lab.research.overfitting import (
    deflated_sharpe_ratio,
    max_cv,
    parameter_stability,
    probability_of_backtest_overfitting,
)


def test_dsr_more_trials_lowers_probability():
    """All else equal, deflating against more trials should give lower prob."""
    few = deflated_sharpe_ratio(1.5, n_trials=10, n_samples=365, annualisation_factor=365)
    many = deflated_sharpe_ratio(1.5, n_trials=1000, n_samples=365, annualisation_factor=365)
    assert many["dsr_probability"] < few["dsr_probability"]
    assert many["expected_max_sharpe"] > few["expected_max_sharpe"]


def test_dsr_weak_sharpe_below_threshold():
    """A near-zero Sharpe after 100 trials should not pass a 0.9 confidence gate."""
    res = deflated_sharpe_ratio(0.3, n_trials=100, n_samples=365, annualisation_factor=365)
    assert res["dsr_probability"] < 0.5


def test_dsr_handles_degenerate_sample():
    res = deflated_sharpe_ratio(1.0, n_trials=10, n_samples=1, annualisation_factor=1)
    assert res["dsr_probability"] == 0.0


def test_pbo_random_matrix_around_half():
    """For an iid-random PnL matrix, PBO should be roughly 0.5 (no real edge)."""
    rng = np.random.default_rng(0)
    mat = rng.normal(size=(10, 20))
    res = probability_of_backtest_overfitting(mat, n_splits=32)
    assert 0.2 <= res["pbo"] <= 0.8


def test_pbo_consistent_winner_low():
    """A configuration that always wins should drive PBO close to 0."""
    rng = np.random.default_rng(0)
    mat = rng.normal(size=(10, 10))
    mat[:, 0] += 5.0  # column 0 dominates every fold
    res = probability_of_backtest_overfitting(mat, n_splits=32)
    assert res["pbo"] < 0.2


def test_pbo_returns_empty_on_small_matrix():
    res = probability_of_backtest_overfitting(np.zeros((2, 2)), n_splits=4)
    assert res["pbo"] == 0.0
    assert res["n_evaluated_splits"] == 0


def test_parameter_stability_basic_stats():
    folds = [
        {"a": 10.0, "b": 1.0},
        {"a": 12.0, "b": 1.1},
        {"a": 11.0, "b": 0.9},
    ]
    stab = parameter_stability(folds)
    assert stab["a"]["mean"] == 11.0
    assert 0.05 < stab["a"]["cv"] < 0.2
    assert max_cv(stab) == max(stab["a"]["cv"], stab["b"]["cv"])


def test_parameter_stability_single_fold_empty():
    assert parameter_stability([]) == {}


def test_parameter_stability_unstable_param():
    folds = [{"x": 10.0}, {"x": 100.0}, {"x": 5.0}, {"x": 150.0}]
    stab = parameter_stability(folds)
    assert stab["x"]["cv"] > 0.5  # clearly unstable
