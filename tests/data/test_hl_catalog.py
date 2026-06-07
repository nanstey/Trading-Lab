"""Round-trip tests for `data.hl_catalog`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_lab.data.hl_catalog import HyperliquidCatalog


@pytest.fixture
def catalog(tmp_path: Path) -> HyperliquidCatalog:
    return HyperliquidCatalog(tmp_path)


def _candle(t_ms: int, close: float = 100.0) -> dict:
    return {
        "t": t_ms,
        "T": t_ms + 3_600_000 - 1,
        "o": "100.0",
        "h": "101.0",
        "l": "99.0",
        "c": str(close),
        "v": "1.5",
        "n": 50,
    }


def test_write_and_read_single_candle(catalog: HyperliquidCatalog):
    n = catalog.write_candles("BTC", "1h", [_candle(1735689600000)])  # 2025-01-01 00:00 UTC
    assert n == 1
    df = catalog.read_candles("BTC", "1h", datetime(2024, 12, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC))
    assert len(df) == 1
    row = df.iloc[0]
    assert row["coin"] == "BTC"
    assert row["interval"] == "1h"
    assert row["close"] == 100.0
    assert row["volume"] == 1.5


def test_writes_are_idempotent(catalog: HyperliquidCatalog):
    bar = _candle(1735689600000, close=100.0)
    catalog.write_candles("BTC", "1h", [bar])
    # Re-write the same bar with a different close — last write wins.
    bar["c"] = "111.0"
    catalog.write_candles("BTC", "1h", [bar])
    df = catalog.read_candles("BTC", "1h", datetime(2024, 12, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC))
    assert len(df) == 1
    assert df.iloc[0]["close"] == 111.0


def test_read_filters_by_range(catalog: HyperliquidCatalog):
    bars = [_candle(1735689600000 + i * 3_600_000) for i in range(48)]  # 48 hourly bars
    catalog.write_candles("ETH", "1h", bars)
    # Read narrower window — first 6 bars.
    df = catalog.read_candles(
        "ETH", "1h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 6, tzinfo=UTC),
    )
    assert len(df) == 7  # 6 hours = 7 bars (start through end, inclusive on both)


def test_summary_reflects_writes(catalog: HyperliquidCatalog):
    catalog.write_candles("BTC", "1d", [_candle(1735689600000)])
    s = catalog.candles_summary("BTC", "1d")
    assert s["rows"] == 1
    assert s["coin"] == "BTC"
    assert s["interval"] == "1d"


def test_read_candles_resamples_4h_from_hourly_when_native_partition_missing(catalog: HyperliquidCatalog):
    bars = [_candle(1735689600000 + i * 3_600_000, close=100.0 + i) for i in range(8)]
    catalog.write_candles("BTC", "1h", bars)

    df = catalog.read_candles(
        "BTC",
        "4h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 8, tzinfo=UTC),
    )

    assert len(df) == 2
    assert df["interval"].tolist() == ["4h", "4h"]
    first = df.iloc[0]
    second = df.iloc[1]
    assert first["ts_open_ms"] == 1735689600000
    assert first["close"] == 103.0
    assert first["volume"] == pytest.approx(6.0)
    assert first["n_trades"] == 200
    assert second["ts_open_ms"] == 1735704000000
    assert second["close"] == 107.0


def test_read_candles_resamples_2h_from_hourly_when_native_partition_missing(catalog: HyperliquidCatalog):
    bars = [_candle(1735689600000 + i * 3_600_000, close=100.0 + i) for i in range(6)]
    catalog.write_candles("BTC", "1h", bars)

    df = catalog.read_candles(
        "BTC",
        "2h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 6, tzinfo=UTC),
    )

    assert len(df) == 3
    assert df["interval"].tolist() == ["2h", "2h", "2h"]
    first = df.iloc[0]
    assert first["ts_open_ms"] == 1735689600000
    assert first["close"] == 101.0
    assert first["volume"] == pytest.approx(3.0)
    assert first["n_trades"] == 100


def test_read_candles_resample_skips_incomplete_4h_buckets(catalog: HyperliquidCatalog):
    bars = [_candle(1735689600000 + i * 3_600_000, close=100.0 + i) for i in range(8)]
    bars.pop(2)  # break the first 4h bucket
    catalog.write_candles("BTC", "1h", bars)

    df = catalog.read_candles(
        "BTC",
        "4h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 8, tzinfo=UTC),
    )

    assert len(df) == 1
    assert df.iloc[0]["ts_open_ms"] == 1735704000000


def test_read_candles_resample_skips_incomplete_2h_buckets(catalog: HyperliquidCatalog):
    bars = [_candle(1735689600000 + i * 3_600_000, close=100.0 + i) for i in range(6)]
    bars.pop(1)  # break the first 2h bucket
    catalog.write_candles("BTC", "1h", bars)

    df = catalog.read_candles(
        "BTC",
        "2h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 6, tzinfo=UTC),
    )

    assert len(df) == 2
    assert df.iloc[0]["ts_open_ms"] == 1735696800000


def test_read_candles_resamples_3h_from_hourly_when_native_partition_missing(catalog: HyperliquidCatalog):
    bars = [_candle(1735689600000 + i * 3_600_000, close=100.0 + i) for i in range(9)]
    catalog.write_candles("ETH", "1h", bars)

    df = catalog.read_candles(
        "ETH",
        "3h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 9, tzinfo=UTC),
    )

    assert len(df) == 3
    assert df["interval"].tolist() == ["3h", "3h", "3h"]
    first = df.iloc[0]
    assert first["ts_open_ms"] == 1735689600000
    assert first["close"] == 102.0
    assert first["volume"] == pytest.approx(4.5)
    assert first["n_trades"] == 150


def test_read_candles_resample_skips_incomplete_3h_buckets(catalog: HyperliquidCatalog):
    bars = [_candle(1735689600000 + i * 3_600_000, close=100.0 + i) for i in range(9)]
    bars.pop(1)  # break the first 3h bucket
    catalog.write_candles("ETH", "1h", bars)

    df = catalog.read_candles(
        "ETH",
        "3h",
        datetime(2025, 1, 1, 0, tzinfo=UTC),
        datetime(2025, 1, 1, 9, tzinfo=UTC),
    )

    assert len(df) == 2
    assert df.iloc[0]["ts_open_ms"] == 1735700400000


def test_funding_round_trip(catalog: HyperliquidCatalog):
    entries = [
        {"time": 1735689600000 + i * 3_600_000,
         "fundingRate": str(0.00001 + i * 1e-6),
         "premium": "0.0001"}
        for i in range(72)
    ]
    n = catalog.write_funding("BTC", entries)
    assert n == 72
    df = catalog.read_funding(
        "BTC",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 5, tzinfo=UTC),
    )
    assert len(df) == 72
    assert df["funding_rate"].iloc[0] == pytest.approx(1e-5)


def test_universe_snapshot_round_trip(catalog: HyperliquidCatalog):
    rows = [
        {"coin": "BTC", "rank": 1, "tier": "tier_1", "day_ntl_vlm": 1e9,
         "open_interest": 1e8, "mark_px": 70_000.0, "funding": 1e-5,
         "sz_decimals": 5, "max_leverage": 40},
        {"coin": "ETH", "rank": 2, "tier": "tier_1", "day_ntl_vlm": 5e8,
         "open_interest": 5e7, "mark_px": 3_500.0, "funding": 1e-5,
         "sz_decimals": 4, "max_leverage": 25},
    ]
    n = catalog.write_universe_snapshot("2026-05-30", rows)
    assert n == 2
    snaps = catalog.list_universe_snapshots()
    assert snaps == ["2026-05-30"]
    df = catalog.read_universe_snapshot("2026-05-30")
    assert len(df) == 2
    assert sorted(df["coin"].tolist()) == ["BTC", "ETH"]
