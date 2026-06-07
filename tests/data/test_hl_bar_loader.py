from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trading_lab.data.hl_bar_loader import load_bars
from trading_lab.data.hl_catalog import HyperliquidCatalog


def _candle(t_ms: int, close: float = 100.0) -> dict:
    high = max(101.0, close + 1.0)
    low = min(99.0, close - 1.0)
    return {
        "t": t_ms,
        "T": t_ms + 3_600_000 - 1,
        "o": "100.0",
        "h": str(high),
        "l": str(low),
        "c": str(close),
        "v": "1.5",
        "n": 50,
    }


def test_load_bars_accepts_3h_interval_from_resampled_hourly(tmp_path: Path) -> None:
    catalog = HyperliquidCatalog(tmp_path)
    bars = [_candle(1735689600000 + i * 3_600_000, close=100.0 + i) for i in range(9)]
    catalog.write_candles("ETH", "1h", bars)

    loaded, instrument = load_bars(
        catalog,
        "ETH",
        "3h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 9, tzinfo=UTC),
    )

    assert instrument.id.value == "ETH-PERP.HYPERLIQUID"
    assert len(loaded) == 3
