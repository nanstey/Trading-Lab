"""
GenericPaperRunner — strategy-class-agnostic paper trading.

Where `PaperRunner` reproduces BinaryArbStrategy's logic in-process, this
runner takes an arbitrary `Strategy` subclass (declared by the hypothesis
frontmatter), feeds it live `OrderBookDeltas` reconstructed from the
Polymarket market WS channel, and intercepts every `submit_order` call
as a paper trade.

This is not a full NautilusTrader node — there's no message bus, no
order state machine, no cache. The strategy gets:
    - `on_start()` called once after `register_instrument()` for each token
    - `on_order_book_deltas(deltas)` called per WS book event
    - `submit_order(order)` and `cancel_order(order)` are no-ops that log

Designed for the agentic-loop end-to-end demo: takes a SMOKE-passed,
optionally-optimised strategy from research/hypotheses/<slug>.md and
proves it produces live signals against real PM data, persisted to
`logs/paper_<slug>_<date>.jsonl`.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nautilus_predict.config import TradingConfig, TradingMode

log = logging.getLogger(__name__)


@dataclass
class PaperSignal:
    ts_iso: str
    slug: str
    token_id: str
    side: str
    price: float
    quantity: float
    client_order_id: str


@dataclass
class GenericPaperSummary:
    slug: str
    strategy_class: str
    instruments: int
    signals_emitted: int = 0
    cancels_emitted: int = 0
    log_path: str = ""
    kill_switch_triggered: bool = False
    signals: list[dict[str, Any]] = field(default_factory=list)


class GenericPaperRunner:
    """
    Strategy-class-agnostic paper runner.

    Parameters
    ----------
    config : TradingConfig
        Must be in PAPER mode.
    slug : str
        Hypothesis slug — used to look up strategy refs and to scope the log.
    strategy_module / strategy_class / strategy_config_class : str
        Where to import the strategy from. Read from the hypothesis MD
        frontmatter when omitted.
    pairs : list[(condition_id, yes_token_id, no_token_id)]
        Markets to subscribe to (and register on the strategy).
    strategy_params : dict
        Kwargs passed to the strategy's *Config.
    duration_secs : int | None
        Auto-stop after N seconds. None = run until cancelled.
    """

    LOG_DIR = Path("logs")

    def __init__(
        self,
        config: TradingConfig,
        slug: str,
        strategy_module: str,
        strategy_class: str,
        strategy_config_class: str,
        pairs: list[tuple[str, str, str]],
        strategy_params: dict[str, Any] | None = None,
        duration_secs: int | None = None,
    ) -> None:
        assert config.trading_mode == TradingMode.PAPER, (
            f"GenericPaperRunner requires TRADING_MODE=paper, got {config.trading_mode}"
        )
        self._config = config
        self._slug = slug
        self._strategy_module = strategy_module
        self._strategy_class = strategy_class
        self._strategy_config_class = strategy_config_class
        self._pairs = pairs
        self._params = strategy_params or {}
        self._duration_secs = duration_secs
        self._stop = asyncio.Event()

        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._log_path = (
            self.LOG_DIR / f"paper_{slug}_{datetime.now(tz=UTC):%Y%m%d}.jsonl"
        )
        self._signals: list[PaperSignal] = []
        self._cancels = 0

        # token_id (raw) → InstrumentId built via make_instrument
        self._token_to_instrument: dict[str, Any] = {}

    async def run(self) -> GenericPaperSummary:
        from nautilus_predict.data.parquet_loader import make_instrument
        from nautilus_predict.risk.kill_switch import KillSwitch

        async def paper_cancel_all() -> None:
            log.info("paper cancel-all (no-op)")

        kill_switch = KillSwitch(
            daily_loss_limit_usdc=self._config.risk.daily_loss_limit_usdc,
            cancel_all_fn=paper_cancel_all,
        )

        # Build instruments + strategy
        for cid, yes_id, no_id in self._pairs:
            self._token_to_instrument[yes_id] = make_instrument(yes_id, cid)
            self._token_to_instrument[no_id] = make_instrument(no_id, cid)

        strategy = self._build_strategy()
        self._install_submit_intercepts(strategy)
        # `on_start` would normally be called by NT. We call it manually.
        for cid, yes_id, no_id in self._pairs:
            # Strategies expose either `register_market_pair` or `register_instrument`.
            yes_iid = self._token_to_instrument[yes_id].id
            no_iid = self._token_to_instrument[no_id].id
            if hasattr(strategy, "register_market_pair"):
                strategy.register_market_pair(cid, yes_iid, no_iid)
            elif hasattr(strategy, "register_instrument"):
                strategy.register_instrument(yes_iid)
                strategy.register_instrument(no_iid)
        # Stub `subscribe_*` so on_start doesn't blow up.
        self._install_strategy_stubs(strategy)
        try:
            strategy.on_start()
        except Exception as exc:
            log.warning("strategy on_start failed: %s", exc)

        log.info(
            "generic paper start | slug=%s class=%s pairs=%d",
            self._slug, self._strategy_class, len(self._pairs),
        )

        token_ids = [t for p in self._pairs for t in (p[1], p[2])]
        ws_task = asyncio.create_task(
            self._stream_market(token_ids, strategy, kill_switch)
        )
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

        try:
            strategy.on_stop()
        except Exception:
            pass

        return GenericPaperSummary(
            slug=self._slug,
            strategy_class=self._strategy_class,
            instruments=len(self._token_to_instrument),
            signals_emitted=len(self._signals),
            cancels_emitted=self._cancels,
            log_path=str(self._log_path),
            kill_switch_triggered=kill_switch.is_triggered,
            signals=[s.__dict__ for s in self._signals],
        )

    def _build_strategy(self):
        from nautilus_predict.runner.backtest import _filter_to_fields

        mod = importlib.import_module(self._strategy_module)
        cls = getattr(mod, self._strategy_class)
        cfg_cls = getattr(mod, self._strategy_config_class)
        cfg = cfg_cls(**_filter_to_fields(cfg_cls, self._params))
        return cls(config=cfg)

    def _install_strategy_stubs(self, strategy) -> None:
        """No-op the subscribe/order-factory bits the strategy expects."""
        noop = lambda *a, **kw: None  # noqa: E731
        for name in (
            "subscribe_order_book_deltas",
            "subscribe_trade_ticks",
            "subscribe_quote_ticks",
            "cancel_all_orders",
        ):
            try:
                object.__setattr__(strategy, name, noop)
            except (AttributeError, TypeError):
                pass

    def _install_submit_intercepts(self, strategy) -> None:
        """Capture submit_order / cancel_order calls instead of routing to a venue."""
        runner = self

        def capture_submit(order) -> None:
            iid = str(getattr(order, "instrument_id", ""))
            token_id = _instrument_to_token(iid, runner._token_to_instrument)
            sig = PaperSignal(
                ts_iso=datetime.now(tz=UTC).isoformat(),
                slug=runner._slug,
                token_id=token_id,
                side=str(getattr(order, "side", "")),
                price=float(getattr(order, "price", 0)),
                quantity=float(getattr(order, "quantity", 0)),
                client_order_id=str(getattr(order, "client_order_id", "")),
            )
            runner._signals.append(sig)
            runner._append_log(sig)
            log.info(
                "paper signal | slug=%s side=%s px=%.2f qty=%.2f token=%s..",
                runner._slug, sig.side, sig.price, sig.quantity, token_id[:14],
            )

        def capture_cancel(*args, **kwargs) -> None:
            runner._cancels += 1
            log.info("paper cancel #%d", runner._cancels)

        try:
            object.__setattr__(strategy, "submit_order", capture_submit)
            object.__setattr__(strategy, "cancel_order", capture_cancel)
        except (AttributeError, TypeError):
            log.warning("could not monkey-patch submit/cancel on strategy")

    async def _timed_stop(self) -> None:
        await asyncio.sleep(self._duration_secs or 0)
        self._stop.set()

    async def _stream_market(self, token_ids, strategy, kill_switch) -> None:
        import websockets

        url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        sub = {"type": "market", "assets_ids": token_ids}
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
                        if isinstance(msg, list):
                            for item in msg:
                                self._dispatch(item, strategy)
                        else:
                            self._dispatch(msg, strategy)
            except Exception as exc:
                if self._stop.is_set():
                    return
                log.warning("paper WS error: %s — reconnect in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _dispatch(self, msg: dict, strategy) -> None:
        ev = msg.get("event_type") or msg.get("type")
        token_id = msg.get("asset_id") or msg.get("market") or ""
        if not token_id or ev not in ("book", "price_change"):
            return
        instrument = self._token_to_instrument.get(token_id)
        if not instrument:
            return

        deltas = self._msg_to_deltas(msg, instrument)
        if not deltas:
            return
        try:
            strategy.on_order_book_deltas(deltas)
        except Exception as exc:
            log.warning("strategy on_order_book_deltas raised: %s", exc)

    def _msg_to_deltas(self, msg: dict, instrument):
        """Convert a WS market-channel message to an OrderBookDeltas event."""
        from nautilus_trader.model.data import BookOrder, OrderBookDelta, OrderBookDeltas
        from nautilus_trader.model.enums import BookAction, OrderSide

        try:
            ts_ms = int(msg.get("timestamp", 0) or 0)
        except (TypeError, ValueError):
            ts_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        ts_ns = ts_ms * 1_000_000

        deltas = [OrderBookDelta.clear(instrument.id, 0, ts_ns, ts_ns)]

        def _ladder(rows, side):
            for r in rows or []:
                try:
                    p = float(r.get("price", 0))
                    s = float(r.get("size", 0))
                except (TypeError, ValueError):
                    continue
                if s <= 0:
                    continue
                p = max(0.01, min(0.99, round(p, 2)))
                order = BookOrder(
                    side,
                    instrument.make_price(p),
                    instrument.make_qty(s),
                    0,
                )
                deltas.append(
                    OrderBookDelta(
                        instrument.id, BookAction.ADD, order, 0, 0, ts_ns, ts_ns
                    )
                )

        _ladder(msg.get("bids"), OrderSide.BUY)
        _ladder(msg.get("asks"), OrderSide.SELL)
        # price_change events use {changes: [{side, price, size}, ...]}.
        for c in msg.get("changes", []) or []:
            try:
                p = max(0.01, min(0.99, round(float(c.get("price", 0)), 2)))
                s = float(c.get("size", 0))
            except (TypeError, ValueError):
                continue
            if s <= 0:
                continue
            side = OrderSide.BUY if c.get("side") == "BUY" else OrderSide.SELL
            order = BookOrder(side, instrument.make_price(p), instrument.make_qty(s), 0)
            deltas.append(
                OrderBookDelta(
                    instrument.id, BookAction.ADD, order, 0, 0, ts_ns, ts_ns
                )
            )

        if len(deltas) == 1:
            return None
        return OrderBookDeltas(instrument_id=instrument.id, deltas=deltas)

    def _append_log(self, sig: PaperSignal) -> None:
        try:
            with self._log_path.open("a") as f:
                f.write(json.dumps(sig.__dict__) + "\n")
        except Exception as exc:
            log.warning("paper log write failed: %s", exc)


def _instrument_to_token(iid_str: str, lookup: dict[str, Any]) -> str:
    for tok, instr in lookup.items():
        if str(instr.id) == iid_str:
            return tok
    return iid_str
