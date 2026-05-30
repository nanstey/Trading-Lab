"""
Anti-overfitting statistics for hyperparameter searches.

Three checks, each cheap, each pulling on a different failure mode:

1. **Deflated Sharpe Ratio** (Bailey & López de Prado, 2014).
   When you sweep N parameter configurations the *expected* maximum Sharpe
   under the null (no edge) is significantly positive. The DSR adjusts the
   observed best-config Sharpe for (a) the number of trials, (b) skew and
   kurtosis of returns, and (c) sample size. Returns the probability that
   the true Sharpe exceeds a benchmark (typically 0).

2. **Probability of Backtest Overfitting (PBO)** via Combinatorially
   Symmetric Cross-Validation (CSCV, Bailey 2017). Split the per-fold
   per-trial PnL matrix into halves: train (the half we'd have picked the
   best on) and test (the held-out half). PBO is the probability that the
   trial best-on-train ranks below median on test, i.e. picking-the-winner
   doesn't generalise. PBO > 0.5 means the search is more likely than not
   producing an overfit choice.

3. **Parameter stability**. For the top-K configurations across WF folds,
   measure the coefficient of variation of each tuned parameter. A
   strategy whose "best" lookback is 20 in fold 1 and 200 in fold 2 isn't
   really optimised — it's fitting noise.

All three return JSON-safe dicts suitable for the experiments DB.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any

import numpy as np
from scipy import stats


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    n_trials: int,
    n_samples: int,
    annualisation_factor: float = 1.0,
    returns_skew: float = 0.0,
    returns_kurtosis: float = 3.0,
    benchmark_sharpe: float = 0.0,
) -> dict[str, float]:
    """
    Compute the Deflated Sharpe Ratio significance probability.

    Parameters
    ----------
    observed_sharpe : float
        Sharpe of the best-of-trials config. Can be per-period or annualised
        — set `annualisation_factor` accordingly so the noise scale matches.
    n_trials : int
        Number of parameter configurations evaluated.
    n_samples : int
        Number of *per-period* observations (e.g. bars/trades) in the equity
        series the Sharpe was computed from.
    annualisation_factor : float
        Per-period→annual scaler used when computing the input Sharpe. E.g.
        365 for crypto-daily, 365*24 for hourly, 252 for stock daily. Pass 1
        if `observed_sharpe` is already per-period. Used to scale the
        deflation correction into the same units as the input.
    returns_skew, returns_kurtosis : float
        Sample skew and (Pearson) kurtosis of the per-period returns.
        Defaults to Gaussian (0, 3). Kurtosis > 3 → fat tails → wider PSR.
    benchmark_sharpe : float
        Sharpe threshold to test against (in same units as `observed_sharpe`).
        Default 0 = "any edge at all".

    Returns
    -------
    dict with keys:
        observed_sharpe        : input echoed
        expected_max_sharpe    : E[max Sharpe | n_trials, null], in input units
        deflated_sharpe        : observed - expected_max
        dsr_probability        : P(true Sharpe > benchmark | observed)
    """
    if n_trials < 1:
        n_trials = 1
    if n_samples < 2:
        return {
            "observed_sharpe": float(observed_sharpe),
            "expected_max_sharpe": 0.0,
            "deflated_sharpe": 0.0,
            "dsr_probability": 0.0,
        }

    af = max(float(annualisation_factor), 1.0)
    # Per-period Sharpe — skew/kurtosis are per-period quantities.
    sr_per = observed_sharpe / math.sqrt(af)
    bench_per = benchmark_sharpe / math.sqrt(af)

    # Per-period noise stdev for Sharpe estimator under null.
    se_per = 1.0 / math.sqrt(n_samples - 1)

    # Expected max of N iid standard normals (Bailey 2014, eq. 7) → scale
    # to per-period Sharpe units.
    em_max_z = _expected_max_normal(n_trials)
    em_max_per = em_max_z * se_per

    sk = float(returns_skew)
    k = float(returns_kurtosis)
    psr_denom = math.sqrt(
        max(1.0 - sk * sr_per + ((k - 1.0) / 4.0) * sr_per ** 2, 1e-12)
    )
    psr_num = (sr_per - bench_per - em_max_per) * math.sqrt(n_samples - 1)
    z = psr_num / psr_denom
    p = float(stats.norm.cdf(z))

    return {
        "observed_sharpe": float(observed_sharpe),
        "expected_max_sharpe": float(em_max_per * math.sqrt(af)),
        "deflated_sharpe": float(observed_sharpe - em_max_per * math.sqrt(af)),
        "dsr_probability": p,
    }


def _expected_max_normal(n: int) -> float:
    """E[max(Z_1..Z_n)] under iid standard normal (Bailey 2014, eq. 7)."""
    # Euler–Mascheroni constant
    gamma_em = 0.5772156649015329
    if n <= 1:
        return 0.0
    inv1 = 1.0 - 1.0 / n
    inv2 = math.exp(-1.0)
    z1 = stats.norm.ppf(inv1)
    z2 = stats.norm.ppf(inv2 * (1.0 - 1.0 / n) + (1.0 - inv2))
    return float((1.0 - gamma_em) * z1 + gamma_em * z2)


def probability_of_backtest_overfitting(
    pnl_matrix: np.ndarray | list[list[float]],
    *,
    n_splits: int = 16,
) -> dict[str, float]:
    """
    Compute PBO via Combinatorially Symmetric Cross-Validation.

    Parameters
    ----------
    pnl_matrix : (S, N) array
        S "sub-periods" (rows) × N "configurations" (cols). Each cell is the
        per-period PnL (or Sharpe) of configuration N in sub-period S.
        Use the WF fold-level Sharpe of each parameter configuration.
    n_splits : int
        Number of random S/2 splits to evaluate. Lower bound is binomial
        (S choose S/2); higher than that just caps work.

    Returns
    -------
    dict with keys:
        pbo                  : probability of overfitting, 0..1
        n_evaluated_splits   : how many splits were averaged
        logits               : list of relative-rank logits (one per split)
    """
    M = np.asarray(pnl_matrix, dtype=float)
    if M.ndim != 2 or M.shape[0] < 4 or M.shape[1] < 2:
        return {"pbo": 0.0, "n_evaluated_splits": 0, "logits": []}

    s, n_configs = M.shape
    half = s // 2
    all_splits = list(combinations(range(s), half))
    if len(all_splits) > n_splits:
        # Deterministic, evenly-spaced subset.
        step = len(all_splits) // n_splits
        all_splits = [all_splits[i * step] for i in range(n_splits)]

    logits: list[float] = []
    overfit_count = 0
    for train_idx in all_splits:
        train_mask = np.zeros(s, dtype=bool)
        train_mask[list(train_idx)] = True
        train = M[train_mask].sum(axis=0)
        test = M[~train_mask].sum(axis=0)

        # Pick the best-on-train config; find its rank-percentile on test.
        best_cfg = int(np.argmax(train))
        # Higher test PnL is better; rank: how many configs the chosen one beats.
        test_rank = float(np.sum(test < test[best_cfg])) / (n_configs - 1) if n_configs > 1 else 0.5
        # Bailey's relative rank: λ in (0,1); overfit if λ < 0.5.
        # Convert to logit for stability and to keep with paper conventions.
        # Add tiny ε so we never log(0).
        eps = 1e-6
        w = max(min(test_rank, 1 - eps), eps)
        logit = math.log(w / (1.0 - w))
        logits.append(logit)
        if test_rank < 0.5:
            overfit_count += 1

    pbo = overfit_count / max(len(all_splits), 1)
    return {
        "pbo": float(pbo),
        "n_evaluated_splits": len(all_splits),
        "logits": [float(x) for x in logits],
    }


def parameter_stability(
    per_fold_best_params: list[dict[str, float]],
) -> dict[str, dict[str, float]]:
    """
    Measure how stable each tuned parameter is across walk-forward folds.

    Parameters
    ----------
    per_fold_best_params : list of dicts
        One entry per fold: the best parameter dict on that fold's training
        portion. All dicts must have the same keys.

    Returns
    -------
    dict[param_name -> {mean, std, cv, range}]
        `cv` = std / |mean|. cv > ~0.5 is a red flag — the optimal value of
        that param wanders too much across folds for the "best" to be a real
        property of the data.
    """
    if not per_fold_best_params:
        return {}

    keys = set(per_fold_best_params[0].keys())
    for p in per_fold_best_params[1:]:
        keys &= set(p.keys())

    out: dict[str, dict[str, float]] = {}
    for k in sorted(keys):
        vals = np.asarray([float(p[k]) for p in per_fold_best_params], dtype=float)
        if vals.size < 2:
            continue
        mean = float(vals.mean())
        std = float(vals.std(ddof=1))
        cv = float(std / abs(mean)) if mean != 0 else float("inf") if std > 0 else 0.0
        out[k] = {
            "mean": mean,
            "std": std,
            "cv": cv if math.isfinite(cv) else 9.99,
            "min": float(vals.min()),
            "max": float(vals.max()),
        }
    return out


def max_cv(stability: dict[str, dict[str, float]]) -> float:
    if not stability:
        return 0.0
    return max(v["cv"] for v in stability.values())


__all__ = [
    "deflated_sharpe_ratio",
    "max_cv",
    "parameter_stability",
    "probability_of_backtest_overfitting",
]
