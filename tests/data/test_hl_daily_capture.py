from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from trading_lab.data.hl_daily_capture import (
    DEFAULT_INTERVALS,
    build_daily_capture_plan,
    choose_capture_coins,
    latest_snapshot_coins,
    normalize_intervals,
)


def test_normalize_intervals_defaults() -> None:
    assert normalize_intervals(None) == DEFAULT_INTERVALS


def test_normalize_intervals_dedupes_preserves_order() -> None:
    assert normalize_intervals(["1h", "5m", "1h", "1d"]) == ("1h", "5m", "1d")


def test_normalize_intervals_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported interval"):
        normalize_intervals(["2h"])


def test_build_daily_capture_plan_uses_expected_overlap_windows() -> None:
    plan = build_daily_capture_plan(as_of=date(2026, 5, 31))

    assert plan.as_of == "2026-05-31"
    assert plan.funding_start_date == "2026-05-01"
    assert plan.funding_end_date == "2026-05-31"

    windows = {w.interval: w for w in plan.windows}
    assert windows["5m"].start_date == "2026-05-24"
    assert windows["1h"].start_date == "2026-05-17"
    assert windows["1d"].start_date == "2026-01-31"
    assert all(w.end_date == "2026-05-31" for w in plan.windows)


def test_choose_capture_coins_prefers_explicit_list() -> None:
    assert choose_capture_coins(
        explicit=["btc", "eth", "btc"],
        latest_snapshot_coins=["SOL"],
        today_snapshot_coins=["NEAR"],
    ) == ["BTC", "ETH"]


def test_choose_capture_coins_unions_latest_and_today_snapshots() -> None:
    assert choose_capture_coins(
        explicit=None,
        latest_snapshot_coins=["BTC", "ETH", "SOL"],
        today_snapshot_coins=["ETH", "NEAR", "BTC"],
    ) == ["BTC", "ETH", "SOL", "NEAR"]


def test_latest_snapshot_coins_reads_latest_snapshot_only() -> None:
    frames = {
        "2026-05-30": pd.DataFrame({"coin": ["BTC", "ETH"]}),
        "2026-05-31": pd.DataFrame({"coin": ["NEAR", "SOL"]}),
    }

    coins = latest_snapshot_coins(["2026-05-30", "2026-05-31"], frames.__getitem__)
    assert coins == ["NEAR", "SOL"]


def test_latest_snapshot_coins_handles_empty_state() -> None:
    assert latest_snapshot_coins([], lambda _snapshot: pd.DataFrame()) == []
