"""
DEPRECATED — superseded by `runner/backtest.py` (`BacktestRunner`) and
`scripts/backtest.py`. This module is preserved as a stub so any external
caller gets a clear pointer instead of an ImportError.
"""

from __future__ import annotations


def run_backtest_session(*args, **kwargs):
    raise NotImplementedError(
        "nautilus_predict.backtest.run_backtest_session is removed. "
        "Use scripts/backtest.py --hypothesis-slug <slug> --start ... --end ..., "
        "or import BacktestRunner from nautilus_predict.runner.backtest."
    )
