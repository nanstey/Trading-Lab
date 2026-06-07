from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from trading_lab.runner.hl_backtest import _resample_equity_to_bars


def test_resample_equity_to_bars_supports_3h_grid() -> None:
    index = pd.to_datetime(
        [
            "2025-01-01T00:00:00Z",
            "2025-01-01T03:00:00Z",
            "2025-01-01T06:00:00Z",
            "2025-01-01T09:00:00Z",
        ],
        utc=True,
    )
    equity = pd.Series([10_000.0, 10_050.0, 10_025.0, 10_075.0], index=index)

    out = _resample_equity_to_bars(
        equity,
        "3h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 9, tzinfo=UTC),
    )

    assert list(out.index) == list(index)
    assert out.iloc[-1] == 10_075.0
