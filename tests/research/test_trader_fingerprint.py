"""Offline fixtures for public-wallet behavioural fingerprints."""

from __future__ import annotations

from trading_lab.research.trader_fingerprint import categorize_market, fingerprint_wallet


def trade(
    condition: str,
    asset: str,
    side: str,
    price: float,
    size: float,
    timestamp: int,
    slug: str = "bitcoin-updown-5m",
) -> dict[str, object]:
    return {
        "conditionId": condition,
        "asset": asset,
        "side": side,
        "price": price,
        "size": size,
        "timestamp": timestamp,
        "slug": slug,
    }


def test_fingerprint_complement_arb_metrics_and_label() -> None:
    trades = [
        trade("m1", "yes", "BUY", 0.45, 100, 100, "election"),
        trade("m1", "no", "BUY", 0.50, 100, 110, "election"),
        trade("m2", "yes", "BUY", 0.40, 50, 200, "election"),
        trade("m2", "no", "BUY", 0.55, 50, 210, "election"),
        trade("m1", "yes", "SELL", 0.60, 100, 400, "election"),
    ]
    result = fingerprint_wallet(trades, [{"conditionId": "m1", "realizedPnl": 12}])

    assert result["notional_usdc"] == {"median": 45.0, "p90": 56.0}
    assert result["complement_arb"]["both_sides_markets"] == 2
    assert result["complement_arb"]["qualifying_markets"] == 2
    assert result["complement_arb"]["qualifying_rate"] == 1.0
    assert result["hold_time_seconds"]["median"] == 300
    assert result["strategy"]["label"] == "complement-arb"
    assert result["win_rate"] == 1.0


def test_fingerprint_favorite_grinder_label_and_histogram() -> None:
    trades = [
        trade("m1", "yes", "BUY", 0.95, 10, 3_600, "sports"),
        trade("m2", "yes", "BUY", 0.91, 10, 3_700, "sports"),
        trade("m3", "yes", "BUY", 0.92, 10, 3_800, "sports"),
        trade("m4", "no", "BUY", 0.93, 10, 3_900, "sports"),
    ]
    result = fingerprint_wallet(trades)

    assert result["entry_prices"]["buy_at_0_9_plus_rate"] == 1.0
    assert result["inter_trade_interval_seconds"]["burst_rate_under_60s"] == 0.0
    assert sum(result["time_of_day_utc"].values()) == 4
    assert result["strategy"]["label"] == "favorite-grinder"


def test_categories_cover_crypto_sports_politics_and_other() -> None:
    assert categorize_market("Bitcoin Up or Down - 5m") == "crypto_short_dated"
    assert categorize_market("NBA finals") == "sports"
    assert categorize_market("US Senate election") == "politics"
    assert categorize_market("Will it rain tomorrow?") == "other"
