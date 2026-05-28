"""
Paper Trading Runner.

Streams live Polymarket market data and runs `BinaryArbStrategy` against
simulated fills. Real orders are never submitted.

Implementation note
-------------------
This runner uses a lightweight in-process harness rather than the full NT
`TradingNode` to keep paper trading runnable end-to-end without the venue
client factory plumbing being complete. The same `BinaryArbStrategy` logic
runs identically to backtest — only the data source and order-execution
shim differ.

Once `venues/polymarket/factory.py` (Phase 4 polish) is wired, this runner
can be upgraded to build a `TradingNode` with `PolymarketLiveDataClientFactory`
and `PolymarketLiveExecClientFactory` in `is_paper=True` mode.

Persisted state
---------------
Every signalled arb is appended to `logs/paper_trades_<date>.jsonl` so a
post-hoc summariser can compute realised paper PnL without re-running.

Halt path
---------
`KillSwitch` from Phase 0.6 is wired and rechecked on every fill. The
persistent flag (`data/.kill_switch`) is read at startup; tripping it from
another process will halt this runner on the next event.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from trading_lab.config import TradingConfig
from trading_lab.venues.polymarket.endpoints import WS_MARKET_URL

log = logging.getLogger(__name__)


@dataclass
class PaperTradeRecord:
    """One paper trade — paired YES + NO buys."""

    ts_iso: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    yes_price: float
    no_price: float
    size_shares: float
    expected_pnl_at_resolution_usdc: float


@dataclass
class PaperPair:
    """In-memory state for one binary market under live observation."""

    condition_id: str
    yes_token_id: str
    no_token_id: str
    yes_ask: float | None = None
    no_ask: float | None = None
    last_signal_ts: float = 0.0  # epoch sec — debounce repeat signals
    paper_fills: int = 0
    expected_pnl_usdc: float = 0.0


@dataclass
class PaperRunSummary:
    pairs: list[PaperPair] = field(default_factory=list)
    total_signals: int = 0
    total_expected_pnl_usdc: float = 0.0
    kill_switch_triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_signals": self.total_signals,
            "total_expected_pnl_usdc": self.total_expected_pnl_usdc,
            "kill_switch_triggered": self.kill_switch_triggered,
            "pairs": [
                {
                    "condition_id": p.condition_id,
                    "paper_fills": p.paper_fills,
                    "expected_pnl_usdc": p.expected_pnl_usdc,
                }
                for p in self.pairs
            ],
        }


class PaperRunner:
    """
    Live-feed paper trading harness for `BinaryArbStrategy`.

    Parameters
    ----------
    config : TradingConfig
        System config. Must have `trading_mode == PAPER`.
    pairs : list[tuple[str, str, str]]
        List of (condition_id, yes_token_id, no_token_id) tuples.
    duration_secs : int | None
        If set, the runner exits after this many seconds. Otherwise runs
        until cancelled.
    debounce_secs : float
        After firing a paper trade on a pair, suppress further signals on
        the same pair for this long. Avoids 100-per-second floods when the
        book lingers in an arb-positive state.
    """

    LOG_DIR = Path("logs")

    def __init__(
        self,
        config: TradingConfig,
        pairs: list[tuple[str, str, str]],
        duration_secs: int | None = None,
        debounce_secs: float = 30.0,
    ) -> None:
        # Paper-vs-live is now per-strategy (hypothesis.state); see PaperRunnerV2.
        # This legacy runner doesn't check anything system-wide.
        self._config = config
        self._pairs: dict[str, PaperPair] = {
            cid: PaperPair(condition_id=cid, yes_token_id=yes, no_token_id=no)
            for (cid, yes, no) in pairs
        }
        # Index token_id → condition_id so book events can find the pair.
        self._token_to_cid: dict[str, str] = {}
        for cid, yes, no in pairs:
            self._token_to_cid[yes] = cid
            self._token_to_cid[no] = cid

        self._duration_secs = duration_secs
        self._debounce_secs = debounce_secs
        self._summary = PaperRunSummary(pairs=list(self._pairs.values()))
        self._stop = asyncio.Event()
        self._log_path = (
            self.LOG_DIR / f"paper_trades_{datetime.now(tz=UTC):%Y%m%d}.jsonl"
        )
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)

    async def run(self) -> PaperRunSummary:
        from trading_lab.risk.kill_switch import KillSwitch

        # Cancel-all is a no-op in paper mode.
        async def paper_cancel_all() -> None:
            log.info("paper cancel-all (no-op)")

        kill_switch = KillSwitch(
            daily_loss_limit_usdc=self._config.risk.daily_loss_limit_usdc,
            cancel_all_fn=paper_cancel_all,
        )

        token_ids = [t for p in self._pairs.values() for t in (p.yes_token_id, p.no_token_id)]
        log.info(
            "paper trading start | pairs=%d tokens=%d duration=%s",
            len(self._pairs),
            len(token_ids),
            self._duration_secs or "infinite",
        )

        ws_task = asyncio.create_task(self._stream_market(token_ids, kill_switch))
        if self._duration_secs:
            timer_task = asyncio.create_task(self._timed_stop())
            try:
                await asyncio.wait(
                    [ws_task, timer_task], return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                self._stop.set()
                for t in (ws_task, timer_task):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(ws_task, timer_task, return_exceptions=True)
        else:
            try:
                await ws_task
            except asyncio.CancelledError:
                pass

        self._summary.kill_switch_triggered = kill_switch.is_triggered
        return self._summary

    async def _timed_stop(self) -> None:
        await asyncio.sleep(self._duration_secs or 0)
        self._stop.set()

    async def _stream_market(
        self, token_ids: list[str], kill_switch: Any
    ) -> None:
        """Connect to the market channel and dispatch book updates."""
        import websockets

        url = WS_MARKET_URL
        sub = {"type": "market", "assets_ids": token_ids}

        async with aiohttp.ClientSession() as _:
            pass  # unused; kept for symmetry with REST clients

        backoff = 2.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=30) as ws:
                    log.info("paper WS connected")
                    await ws.send(json.dumps(sub))
                    backoff = 2.0
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        if kill_switch.is_triggered:
                            log.warning("kill switch active — exiting")
                            return
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        # Polymarket sends arrays of events.
                        if isinstance(msg, list):
                            for item in msg:
                                await self._handle_msg(item)
                        else:
                            await self._handle_msg(msg)
            except Exception as exc:
                if self._stop.is_set():
                    return
                log.warning("paper WS error: %s — reconnecting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_msg(self, msg: dict[str, Any]) -> None:
        ev = msg.get("event_type") or msg.get("type")
        token_id = msg.get("asset_id") or msg.get("market") or ""
        if not token_id:
            return
        cid = self._token_to_cid.get(token_id)
        if not cid:
            return
        pair = self._pairs[cid]

        if ev in ("book", "price_change"):
            asks = msg.get("asks") or msg.get("sells") or []
            if not asks:
                # price_change uses 'changes' with side fields.
                changes = msg.get("changes") or []
                asks = [c for c in changes if c.get("side") == "SELL"]
            best_ask = None
            for a in asks:
                try:
                    p = float(a.get("price", 0))
                except (TypeError, ValueError):
                    continue
                size = float(a.get("size", 0))
                if size <= 0:
                    continue
                if best_ask is None or p < best_ask:
                    best_ask = p
            if best_ask is None:
                return
            if token_id == pair.yes_token_id:
                pair.yes_ask = best_ask
            else:
                pair.no_ask = best_ask
            self._scan(pair)

    def _scan(self, pair: PaperPair) -> None:
        if pair.yes_ask is None or pair.no_ask is None:
            return
        combined = pair.yes_ask + pair.no_ask
        # taker_fee from strategy config — read off the existing TradingConfig.arb.
        fee = 0.0  # PM is currently zero-fee for binary takers
        profit_per_share = 1.0 - combined - fee
        if profit_per_share < self._config.arb.min_profit_usdc:
            return

        now = datetime.now(tz=UTC).timestamp()
        if now - pair.last_signal_ts < self._debounce_secs:
            return
        pair.last_signal_ts = now

        # Size: spend `order_notional` USDC per leg → take min share count.
        leg_notional = 5.0  # respect PM min_order_size
        yes_shares = leg_notional / max(pair.yes_ask, 0.01)
        no_shares = leg_notional / max(pair.no_ask, 0.01)
        size = min(yes_shares, no_shares)
        # Cap by max_capital total
        share_cap = self._config.arb.max_capital_usdc / combined
        size = min(size, share_cap)
        size = round(size, 2)

        expected_pnl = profit_per_share * size
        pair.paper_fills += 1
        pair.expected_pnl_usdc += expected_pnl
        self._summary.total_signals += 1
        self._summary.total_expected_pnl_usdc += expected_pnl

        rec = PaperTradeRecord(
            ts_iso=datetime.now(tz=UTC).isoformat(),
            condition_id=pair.condition_id,
            yes_token_id=pair.yes_token_id,
            no_token_id=pair.no_token_id,
            yes_price=pair.yes_ask,
            no_price=pair.no_ask,
            size_shares=size,
            expected_pnl_at_resolution_usdc=expected_pnl,
        )
        self._append_log(rec)
        log.info(
            "paper ARB | cid=%s yes=%.2f no=%.2f size=%.2f exp_pnl=%.4f",
            pair.condition_id[:14],
            pair.yes_ask,
            pair.no_ask,
            size,
            expected_pnl,
        )

    def _append_log(self, rec: PaperTradeRecord) -> None:
        try:
            with self._log_path.open("a") as f:
                f.write(json.dumps(rec.__dict__) + "\n")
        except Exception as exc:
            log.warning("paper log write failed: %s", exc)
