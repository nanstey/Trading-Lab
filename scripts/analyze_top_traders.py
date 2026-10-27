#!/usr/bin/env python3
"""Profile publicly visible Polymarket leader-board wallets without private data.

Writes one JSON fingerprint per included wallet and a Markdown comparison table.
The command never creates hypotheses or writes the lifecycle database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.research.trader_fingerprint import fingerprint_wallet

DATA_API = "https://data-api.polymarket.com"
PERIOD_DAYS = {"DAY": 1, "WEEK": 7, "MONTH": 30, "ALL": None}
log = logging.getLogger("analyze_top_traders")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=PERIOD_DAYS, required=True)
    parser.add_argument("--top", type=int, required=True, help="Leaderboard wallets to inspect")
    parser.add_argument("--out", type=Path, required=True, help="Output directory")
    parser.add_argument("--min-trades", type=int, default=1, help="Skip wallets below this count")
    parser.add_argument("--db", type=Path, default=Path("data/market_catalog.db"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/trader_cache"))
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--request-delay-secs", type=float, default=0.2)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


class PublicApi:
    def __init__(self, session: aiohttp.ClientSession, cache_dir: Path, delay: float) -> None:
        self.session = session
        self.cache_dir = cache_dir
        self.delay = delay

    async def get(self, path: str, params: dict[str, str]) -> Any:
        for attempt in range(4):
            try:
                async with self.session.get(f"{DATA_API}{path}", params=params) as response:
                    if response.status in {429, 500, 502, 503, 504}:
                        await asyncio.sleep(2**attempt)
                        continue
                    response.raise_for_status()
                    result = await response.json()
                    await asyncio.sleep(self.delay)
                    return result
            except aiohttp.ClientError as exc:
                if attempt == 3:
                    raise RuntimeError(f"GET {path} failed: {exc}") from exc
                await asyncio.sleep(2**attempt)
        raise RuntimeError(f"GET {path} exhausted retries")

    def cache(self, wallet: str, name: str, data: Any) -> None:
        path = self.cache_dir / wallet.lower() / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    async def optional(self, path: str, wallet: str) -> list[dict[str, Any]]:
        try:
            data = await self.get(path, {"user": wallet, "limit": "500", "offset": "0"})
        except RuntimeError as exc:
            log.warning("optional endpoint unavailable path=%s wallet=%s error=%s", path, wallet, exc)
            return []
        return data if isinstance(data, list) else []

    async def trades(self, wallet: str, cutoff: int | None, page_size: int) -> list[dict[str, Any]]:
        """Page newest-first data API history, switching to a `before` cursor at offset caps."""
        output: list[dict[str, Any]] = []
        offset = 0
        before: int | None = None
        oldest = 2**63 - 1
        seen: set[tuple[str, int, str, str]] = set()
        while True:
            params = {"user": wallet, "limit": str(page_size), "offset": str(offset)}
            if before is not None:
                params["before"] = str(before)
            try:
                page = await self.get("/trades", params)
            except RuntimeError as exc:
                if before is None and offset > 0:
                    before, offset = oldest - 1, 0
                    log.info("trade offset cap wallet=%s; switching to before cursor", wallet)
                    continue
                raise exc
            if not isinstance(page, list) or not page:
                break
            page_oldest = oldest
            for row in page:
                if not isinstance(row, dict):
                    continue
                timestamp = int(float(row.get("timestamp") or 0))
                page_oldest = min(page_oldest, timestamp)
                if cutoff is not None and timestamp < cutoff:
                    continue
                key = (str(row.get("transactionHash") or ""), timestamp, str(row.get("asset") or ""), str(row.get("side") or ""))
                if key not in seen:
                    seen.add(key)
                    output.append(row)
            oldest = min(oldest, page_oldest)
            if cutoff is not None and page_oldest < cutoff:
                break
            if len(page) < page_size:
                break
            offset += page_size
            # The API commonly refuses offsets around 3500. Start another
            # timestamp window, including when an earlier window also reaches
            # the cap. A non-advancing cursor means upstream ignored `before`.
            if offset >= 3_500:
                if oldest == 2**63 - 1 or (before is not None and page_oldest >= before):
                    log.warning("trade cursor did not advance wallet=%s", wallet)
                    break
                before, offset = oldest - 1, 0
        self.cache(wallet, "trades", output)
        return output


def catalog_text(db_path: Path, condition_ids: set[str]) -> dict[str, str]:
    if not db_path.exists():
        return {}
    from trading_lab.data.market_catalog import MarketCatalog

    catalog = MarketCatalog(db_path)
    try:
        return {
            condition_id: " ".join(
                value for value in (row.question, row.category, row.event_slug, row.event_title) if value
            )
            for condition_id in condition_ids
            if (row := catalog.get_market(condition_id)) is not None
        }
    finally:
        catalog.close()


def write_summary(out_dir: Path, profiles: list[dict[str, Any]]) -> None:
    lines = [
        "# Top Polymarket Trader Fingerprints",
        "",
        "Public fills only. Labels are rule-based behavioural hypotheses, not verified strategy attribution.",
        "",
        "| Rank | Wallet | Name | Label | Trades | PnL | Complement rate | Buy rate | Top-5 PnL share |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for profile in profiles:
        fp = profile["fingerprint"]
        lines.append(
            f"| {profile['rank']} | `{profile['wallet']}` | {profile['user_name'] or '-'} | "
            f"{fp['strategy']['label']} | {fp['trade_count']} | {profile['leaderboard_pnl']:.2f} | "
            f"{fp['complement_arb']['qualifying_rate']:.2%} | {fp['sides']['buy_rate']:.2%} | "
            f"{fp['top_5_market_pnl_share']:.2%} |"
        )
    candidates = [p for p in profiles if p["fingerprint"]["strategy"]["label"] != "other"]
    lines.extend(["", "## Hypothesis candidates", ""])
    if candidates:
        for profile in candidates:
            fp = profile["fingerprint"]
            lines.append(
                f"- `{profile['wallet']}`: **{fp['strategy']['label']}**. Evidence: "
                f"{json.dumps(fp['strategy']['evidence'], sort_keys=True)}"
            )
    else:
        lines.append("No wallet passed a non-`other` rule. Do not create a hypothesis from this run.")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    if args.top < 1 or args.min_trades < 0 or args.page_size < 1:
        raise ValueError("--top and --page-size must be positive; --min-trades cannot be negative")
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    cutoff_days = PERIOD_DAYS[args.period]
    cutoff = int((datetime.now(tz=UTC) - timedelta(days=cutoff_days)).timestamp()) if cutoff_days else None
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        api = PublicApi(session, args.cache_dir, args.request_delay_secs)
        leaderboard = await api.get("/v1/leaderboard", {"timePeriod": args.period, "orderBy": "PNL", "limit": str(args.top)})
        if not isinstance(leaderboard, list):
            raise RuntimeError("leaderboard returned a non-list response")
        profiles: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in leaderboard:
            wallet = str(row.get("proxyWallet") or "")
            if not wallet:
                continue
            trades = await api.trades(wallet, cutoff, args.page_size)
            positions = await api.optional("/positions", wallet)
            activity = await api.optional("/activity", wallet)
            api.cache(wallet, "positions", positions)
            api.cache(wallet, "activity", activity)
            if len(trades) < args.min_trades:
                skipped.append({"wallet": wallet, "trade_count": len(trades), "reason": "min_trades"})
                continue
            text = catalog_text(args.db, {str(t.get("conditionId") or "") for t in trades})
            fp = fingerprint_wallet(trades, positions, text)
            profile = {
                "rank": int(row.get("rank") or 0),
                "wallet": wallet,
                "user_name": str(row.get("userName") or ""),
                "leaderboard_volume": float(row.get("vol") or 0),
                "leaderboard_pnl": float(row.get("pnl") or 0),
                "period": args.period,
                "trade_data_cutoff_utc": datetime.fromtimestamp(cutoff, tz=UTC).isoformat() if cutoff else None,
                "api_fields": {"positions": bool(positions), "activity": bool(activity)},
                "fingerprint": fp,
            }
            (args.out / f"{wallet.lower()}.json").write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")
            profiles.append(profile)
    profiles.sort(key=lambda item: item["rank"])
    write_summary(args.out, profiles)
    result = {
        "period": args.period,
        "leaderboard_rows": len(leaderboard),
        "profiled_wallets": len(profiles),
        "skipped": skipped,
        "out": str(args.out),
        "summary": str(args.out / "summary.md"),
        "hypothesis_candidates": [p["wallet"] for p in profiles if p["fingerprint"]["strategy"]["label"] != "other"],
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = perf_counter()
    try:
        result = asyncio.run(run(args))
    except (RuntimeError, ValueError, aiohttp.ClientError) as exc:
        log.error("analysis failed: %s", exc)
        return 1
    log.info("analysis completed in %.2fs", perf_counter() - started)
    return result


if __name__ == "__main__":
    sys.exit(main())
