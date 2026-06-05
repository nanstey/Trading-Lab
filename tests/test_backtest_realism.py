from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pandas as pd


backtest = importlib.import_module("trading_lab.runner.backtest")


class _FakeCatalog:
    def __init__(self, orderbook_df: pd.DataFrame):
        self._orderbook_df = orderbook_df

    def read_orderbook_history(self, token_id: str, start: datetime, end: datetime) -> pd.DataFrame:
        return self._orderbook_df.copy()


START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 2, tzinfo=UTC)


def test_load_execution_inputs_prefers_snapshots(monkeypatch) -> None:
    snapshot_deltas = ["snapshot-delta"]
    reconstructed = ["reconstructed-delta"]
    monkeypatch.setattr(backtest, "load_book_as_order_book_deltas", lambda *args, **kwargs: snapshot_deltas)
    monkeypatch.setattr(backtest, "reconstruct_book_from_trades", lambda *args, **kwargs: reconstructed)

    orderbook_df = pd.DataFrame(
        [
            {"timestamp": 1, "side": "bid", "price": 0.44, "size": 25.0},
            {"timestamp": 1, "side": "ask", "price": 0.46, "size": 20.0},
        ]
    )
    deltas, realism = backtest._load_execution_inputs(
        catalog=_FakeCatalog(orderbook_df),
        token_id="t1",
        instrument=object(),
        start=START,
        end=END,
        order_notional_usdc=5.0,
    )

    assert deltas == snapshot_deltas
    assert realism["book_source"] == "snapshots"
    assert realism["used_reconstructed_book"] is False
    assert realism["snapshot_groups"] == 1
    assert realism["median_visible_notional_usdc"] > 0
    assert realism["prob_fill_on_limit"] == 0.5


def test_load_execution_inputs_falls_back_to_reconstructed_books(monkeypatch) -> None:
    monkeypatch.setattr(backtest, "load_book_as_order_book_deltas", lambda *args, **kwargs: [])
    monkeypatch.setattr(backtest, "reconstruct_book_from_trades", lambda *args, **kwargs: ["reconstructed-delta"])

    deltas, realism = backtest._load_execution_inputs(
        catalog=_FakeCatalog(pd.DataFrame()),
        token_id="t1",
        instrument=object(),
        start=START,
        end=END,
        order_notional_usdc=5.0,
    )

    assert deltas == ["reconstructed-delta"]
    assert realism["book_source"] == "reconstructed_trades"
    assert realism["used_reconstructed_book"] is True
    assert "reconstructed_book_fallback" in realism["warnings"]
    assert realism["prob_fill_on_limit"] < 0.5
    assert realism["prob_slippage"] > 0.5


def test_execution_realism_penalizes_shallow_visible_depth() -> None:
    orderbook_df = pd.DataFrame(
        [
            {"timestamp": 1, "side": "bid", "price": 0.44, "size": 3.0},
            {"timestamp": 1, "side": "ask", "price": 0.46, "size": 3.0},
            {"timestamp": 2, "side": "bid", "price": 0.45, "size": 4.0},
            {"timestamp": 2, "side": "ask", "price": 0.47, "size": 4.0},
        ]
    )
    realism = backtest._execution_realism_from_orderbook_df(
        orderbook_df,
        order_notional_usdc=5.0,
        book_source="snapshots",
    )

    assert realism["median_visible_notional_usdc"] < 5.0
    assert realism["depth_penalty"] < 1.0
    assert realism["prob_fill_on_limit"] < 0.5
    assert realism["prob_slippage"] > 0.5
    assert "shallow_visible_depth" in realism["warnings"]
