from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from trading_lab.data.catalog import DataCatalog
from trading_lab.data.hl_catalog import HyperliquidCatalog

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cross_venue_backtest = _load_script_module(
    "cross_venue_backtest",
    "scripts/cross_venue_backtest.py",
)


VALID_SPEC = """---
slug: hl-pm-btc-basis
venue: cross_venue
cross_venue:
  polymarket:
    condition_id: 0xabc
    yes_token_id: 111
    no_token_id: 222
  hyperliquid:
    kind: perp
    symbol: BTC
    network: testnet
strategy_module: trading_lab.strategies.cross_venue_observe
strategy_class: CrossVenueObserveStrategy
strategy_config_class: CrossVenueObserveConfig
---
"""


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


def test_build_cross_venue_backtest_report_merges_pm_and_hl_timelines(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(VALID_SPEC)
    data_dir = tmp_path / "parquet"
    pm = DataCatalog(data_dir)
    hl = HyperliquidCatalog(data_dir)
    start = datetime(2025, 1, 1, 0, tzinfo=UTC)
    end = datetime(2025, 1, 1, 3, tzinfo=UTC)

    pm.write_trades("111", [
        {"timestamp": 1735689600000, "trade_id": "y1", "side": "BUY", "price": 0.51, "size": 10, "fee": 0.0},
        {"timestamp": 1735693200000, "trade_id": "y2", "side": "BUY", "price": 0.52, "size": 12, "fee": 0.0},
    ])
    pm.write_trades("222", [
        {"timestamp": 1735689900000, "trade_id": "n1", "side": "SELL", "price": 0.48, "size": 9, "fee": 0.0},
    ])
    hl.write_candles("BTC", "1h", [
        _candle(1735689600000, close=100.0),
        _candle(1735693200000, close=101.0),
    ])

    report = cross_venue_backtest.build_cross_venue_backtest_report(
        spec_path,
        start=start,
        end=end,
        data_dir=data_dir,
        hl_interval="1h",
    )

    assert report["ok"] is True
    assert report["slug"] == "hl-pm-btc-basis"
    assert report["timeline_counts"] == {
        "polymarket_yes_trades": 2,
        "polymarket_no_trades": 1,
        "hyperliquid_bars": 2,
        "unified_events": 5,
    }
    assert report["alignment"]["overlap_ok"] is True
    assert report["alignment"]["gaps"] == []
    assert report["timeline_preview"][0]["source"] == "polymarket_yes"
    assert report["timeline_preview"][-1]["source"] in {"polymarket_yes", "hyperliquid"}



def test_build_cross_venue_backtest_report_flags_no_overlap_window(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(VALID_SPEC)
    data_dir = tmp_path / "parquet"
    pm = DataCatalog(data_dir)
    hl = HyperliquidCatalog(data_dir)
    start = datetime(2025, 1, 1, 0, tzinfo=UTC)
    end = datetime(2025, 1, 2, 0, tzinfo=UTC)

    pm.write_trades("111", [
        {"timestamp": 1735689600000, "trade_id": "y1", "side": "BUY", "price": 0.51, "size": 10, "fee": 0.0},
    ])
    pm.write_trades("222", [
        {"timestamp": 1735689660000, "trade_id": "n1", "side": "SELL", "price": 0.48, "size": 9, "fee": 0.0},
    ])
    hl.write_candles("BTC", "1h", [
        _candle(1735776000000, close=103.0),
    ])

    report = cross_venue_backtest.build_cross_venue_backtest_report(
        spec_path,
        start=start,
        end=end,
        data_dir=data_dir,
        hl_interval="1h",
    )

    assert report["ok"] is True
    assert report["alignment"]["overlap_ok"] is False
    assert "no_cross_venue_overlap_window" in report["alignment"]["gaps"]



def test_cross_venue_backtest_report_is_json_serializable(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(VALID_SPEC)
    data_dir = tmp_path / "parquet"
    start = datetime(2025, 1, 1, 0, tzinfo=UTC)
    end = datetime(2025, 1, 1, 1, tzinfo=UTC)

    report = cross_venue_backtest.build_cross_venue_backtest_report(
        spec_path,
        start=start,
        end=end,
        data_dir=data_dir,
        hl_interval="1h",
    )
    encoded = json.dumps(report)

    assert '"alignment"' in encoded
    assert report["timeline_counts"]["unified_events"] == 0
