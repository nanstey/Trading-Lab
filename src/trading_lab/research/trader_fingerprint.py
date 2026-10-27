"""Public-trade fingerprints for Polymarket wallets.

The data API exposes fills, not orders or order-book queue position.  Metrics in
this module therefore describe observable trading behaviour and label evidence;
they do not claim to identify a trader's private strategy or execution role.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from statistics import median
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timestamp(trade: dict[str, Any]) -> int:
    return int(_number(trade.get("timestamp")))


def _notional(trade: dict[str, Any]) -> float:
    return abs(_number(trade.get("size")) * _number(trade.get("price")))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = (len(values) - 1) * percentile
    lower, upper = int(index), min(int(index) + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0


def categorize_market(text: str) -> str:
    """Classify public market text into broad research buckets."""
    lower = text.lower()
    if any(word in lower for word in ("5m", "15m", "updown", "up-down", "bitcoin", "btc", "ethereum", "eth")):
        return "crypto_short_dated"
    if any(word in lower for word in ("nba", "nfl", "mlb", "nhl", "soccer", "football", "tennis", "fifa", "spread")):
        return "sports"
    if any(word in lower for word in ("president", "election", "senate", "congress", "democrat", "republican", "trump", "politic")):
        return "politics"
    return "other"


def _market_text(trade: dict[str, Any], market_text: dict[str, str]) -> str:
    condition_id = str(trade.get("conditionId") or "")
    return " ".join(
        item for item in (
            market_text.get(condition_id, ""),
            str(trade.get("title") or ""),
            str(trade.get("slug") or ""),
            str(trade.get("eventSlug") or ""),
        ) if item
    )


def _hold_times(trades: list[dict[str, Any]]) -> list[float]:
    """FIFO estimated holds for matched BUY then SELL lots of one outcome asset."""
    lots: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
    holds: list[float] = []
    for trade in sorted(trades, key=_timestamp):
        asset = str(trade.get("asset") or "")
        size = abs(_number(trade.get("size")))
        if not asset or not size:
            continue
        if str(trade.get("side", "")).upper() == "BUY":
            lots[asset].append((size, _timestamp(trade)))
            continue
        if str(trade.get("side", "")).upper() != "SELL":
            continue
        remaining = size
        while remaining and lots[asset]:
            lot_size, bought_at = lots[asset][0]
            matched = min(remaining, lot_size)
            if matched:
                holds.append(max(0, _timestamp(trade) - bought_at))
            remaining -= matched
            if matched == lot_size:
                lots[asset].popleft()
            else:
                lots[asset][0] = (lot_size - matched, bought_at)
    return holds


def _complement_stats(trades: list[dict[str, Any]]) -> tuple[int, int, float]:
    """Find conditions with buys of two outcomes whose VWAPs sum below one."""
    buys: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    for trade in trades:
        if str(trade.get("side", "")).upper() != "BUY":
            continue
        condition, asset = str(trade.get("conditionId") or ""), str(trade.get("asset") or "")
        if condition and asset:
            buys[condition][asset].append((_number(trade.get("size")), _number(trade.get("price"))))
    two_sided = 0
    qualifying = 0
    edges: list[float] = []
    for outcomes in buys.values():
        if len(outcomes) < 2:
            continue
        two_sided += 1
        prices = []
        for fills in outcomes.values():
            quantity = sum(size for size, _ in fills)
            prices.append(sum(size * price for size, price in fills) / quantity if quantity else 1.0)
        best_pair = sum(sorted(prices)[:2])
        if best_pair < 1.0:
            qualifying += 1
            edges.append(1.0 - best_pair)
    return two_sided, qualifying, round(median(edges), 4) if edges else 0.0


def _position_pnl(positions: list[dict[str, Any]]) -> tuple[dict[str, float], float]:
    by_market: dict[str, float] = defaultdict(float)
    for position in positions:
        condition = str(position.get("conditionId") or "")
        if condition:
            by_market[condition] += _number(position.get("realizedPnl"))
    pnl = list(by_market.values())
    return dict(by_market), _ratio(sum(value > 0 for value in pnl), len(pnl))


def label_strategy(metrics: dict[str, Any]) -> dict[str, Any]:
    """Assign one conservative label and the threshold facts that support it."""
    evidence = {
        "complement_arb_rate": metrics["complement_arb"]["qualifying_rate"],
        "favorite_buy_rate": metrics["entry_prices"]["buy_at_0_9_plus_rate"],
        "buy_sell_balance": metrics["sides"]["balance"],
        "median_hold_seconds": metrics["hold_time_seconds"]["median"],
        "crypto_short_dated_rate": metrics["market_categories"].get("crypto_short_dated", 0.0),
        "trade_count": metrics["trade_count"],
    }
    if evidence["complement_arb_rate"] >= 0.25 and metrics["complement_arb"]["qualifying_markets"] >= 2:
        label = "complement-arb"
    elif evidence["favorite_buy_rate"] >= 0.65 and metrics["sides"]["buy_rate"] >= 0.65:
        label = "favorite-grinder"
    elif (evidence["buy_sell_balance"] >= 0.75 and evidence["median_hold_seconds"] <= 86_400
          and metrics["trade_count"] >= 30):
        label = "market-maker"
    elif evidence["crypto_short_dated_rate"] >= 0.6 and evidence["median_hold_seconds"] <= 3_600:
        label = "latency/momentum on crypto short-dated"
    elif metrics["sides"]["buy_rate"] >= 0.75 or metrics["sides"]["sell_rate"] >= 0.75:
        label = "directional/informed"
    else:
        label = "other"
    return {"label": label, "evidence": evidence}


def fingerprint_wallet(
    trades: list[dict[str, Any]],
    positions: list[dict[str, Any]] | None = None,
    market_text: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compute an explainable behavioural fingerprint from public API payloads."""
    positions = positions or []
    market_text = market_text or {}
    clean_trades = [trade for trade in trades if isinstance(trade, dict)]
    notionals = [_notional(trade) for trade in clean_trades]
    buys = sum(str(trade.get("side", "")).upper() == "BUY" for trade in clean_trades)
    sells = sum(str(trade.get("side", "")).upper() == "SELL" for trade in clean_trades)
    buy_prices = [_number(t.get("price")) for t in clean_trades if str(t.get("side", "")).upper() == "BUY"]
    timestamps = sorted(_timestamp(t) for t in clean_trades if _timestamp(t))
    intervals = [later - earlier for earlier, later in zip(timestamps, timestamps[1:], strict=False)]
    holds = _hold_times(clean_trades)
    hours = Counter(datetime.fromtimestamp(ts, tz=UTC).hour for ts in timestamps)
    categories = Counter(categorize_market(_market_text(t, market_text)) for t in clean_trades)
    total_category_trades = sum(categories.values())
    two_sided, complement_markets, complement_edge = _complement_stats(clean_trades)
    pnl_by_market, win_rate = _position_pnl(positions)
    absolute_pnl = sum(abs(pnl) for pnl in pnl_by_market.values())
    top_five = sorted((abs(pnl) for pnl in pnl_by_market.values()), reverse=True)[:5]
    side_balance = round(1 - abs(buys - sells) / max(buys + sells, 1), 4)
    median_hold = round(median(holds), 2) if holds else 0.0
    maker_proxy = (
        "liquidity_provider_like"
        if side_balance >= 0.75 and 0 < median_hold <= 86_400
        else "aggressive_or_unresolved"
    )
    metrics: dict[str, Any] = {
        "trade_count": len(clean_trades),
        "notional_usdc": {"median": round(median(notionals), 4) if notionals else 0.0, "p90": round(_percentile(notionals, 0.9), 4)},
        "sides": {"buy_count": buys, "sell_count": sells, "buy_rate": _ratio(buys, len(clean_trades)), "sell_rate": _ratio(sells, len(clean_trades)), "balance": side_balance},
        "complement_arb": {"both_sides_markets": two_sided, "qualifying_markets": complement_markets, "qualifying_rate": _ratio(complement_markets, two_sided), "median_implied_edge": complement_edge},
        "maker_taker_proxy": {"value": maker_proxy, "confidence": "low", "reason": "public trade rows do not expose maker/taker or order type; this uses buy/sell balance and estimated matched hold time"},
        "hold_time_seconds": {"matched_lots": len(holds), "median": median_hold, "p90": round(_percentile(holds, 0.9), 2)},
        "time_of_day_utc": {str(hour): hours.get(hour, 0) for hour in range(24)},
        "inter_trade_interval_seconds": {"median": round(median(intervals), 2) if intervals else 0.0, "p90": round(_percentile(intervals, 0.9), 2), "burst_rate_under_60s": _ratio(sum(i < 60 for i in intervals), len(intervals))},
        "market_categories": {name: _ratio(count, total_category_trades) for name, count in sorted(categories.items())},
        "entry_prices": {"buy_median": round(median(buy_prices), 4) if buy_prices else 0.0, "buy_at_0_9_plus_rate": _ratio(sum(p >= 0.9 for p in buy_prices), len(buy_prices)), "buy_at_0_1_or_less_rate": _ratio(sum(p <= 0.1 for p in buy_prices), len(buy_prices))},
        "realized_pnl_by_market": pnl_by_market,
        "win_rate": win_rate,
        "top_5_market_pnl_share": round(sum(top_five) / absolute_pnl, 4) if absolute_pnl else 0.0,
    }
    metrics["strategy"] = label_strategy(metrics)
    return metrics
