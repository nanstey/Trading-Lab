"""
Polymarket live data client (NautilusTrader DataClient implementation).

Subscribes to Polymarket's WebSocket market channel and translates raw
messages into NautilusTrader domain objects (OrderBookDelta, TradeTick, etc.)
which are then published onto the internal message bus.
"""

from __future__ import annotations

from typing import Any

import structlog
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import OrderBookDelta, OrderBookDeltas
from nautilus_trader.model.enums import BookAction, OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.objects import Price, Quantity

from trading_lab.venues.polymarket.client import PolymarketRestClient, PolymarketWsClient

log = structlog.get_logger(__name__)

VENUE = "POLYMARKET"


class PolymarketDataClient(LiveMarketDataClient):
    """
    NautilusTrader data client for Polymarket.

    Lifecycle:
    1. _connect()   - authenticate, fetch instrument info, open WebSocket
    2. _disconnect() - gracefully close WebSocket
    """

    def __init__(
        self,
        loop,
        client: PolymarketRestClient,
        ws_client: PolymarketWsClient,
        msgbus,
        cache,
        clock,
        instrument_provider=None,
    ) -> None:
        # NT requires an InstrumentProvider on LiveMarketDataClient — even a
        # stub one is fine for our usage (instruments are pre-loaded into the
        # cache directly by the runner).
        if instrument_provider is None:
            from nautilus_trader.common.providers import InstrumentProvider
            from nautilus_trader.config import InstrumentProviderConfig
            instrument_provider = InstrumentProvider(
                config=InstrumentProviderConfig(),
            )
        from nautilus_trader.model.identifiers import Venue
        super().__init__(
            loop=loop,
            client_id=ClientId(VENUE),
            venue=Venue(VENUE),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
        )
        self._rest = client
        self._ws = ws_client
        # The runner pushes a map of {short_symbol: full_token_id} into this
        # dict before the node starts; lets us recover the full token
        # needed to subscribe to PM's market WS from a NT InstrumentId.
        self._symbol_to_token: dict[str, str] = {}

    async def _connect(self) -> None:
        log.info("PolymarketDataClient connecting")
        self._ws.start()

    async def _subscribe_order_book_deltas(self, command) -> None:
        """NT-side hook for `strategy.subscribe_order_book_deltas`."""
        iid = command.instrument_id
        token = self._token_for_instrument(iid)
        if not token:
            log.warning(
                "subscribe_order_book_deltas: cannot recover token from %s", iid,
            )
            return
        log.info("PolymarketDataClient subscribe book deltas", token=token[:16])
        self._ws.subscribe_market([token])

    async def _subscribe_trade_ticks(self, command) -> None:
        """
        Trade-tick subscription on Polymarket market channel.

        PM's market WS doesn't expose standalone trade-print events; trade
        prints arrive as the `last_trade_price` field of `book` snapshots
        and in `price_change` events. We still subscribe to the market
        channel for this instrument's token so book events flow in (and
        `_handle_book_event` / `_handle_trade_event` will publish ticks
        from there). The strategy's `Symbol` is the short-token convention
        — we need to recover the full token_id to subscribe. Look it up
        from the instrument in the cache.
        """
        iid = command.instrument_id
        token = self._token_for_instrument(iid)
        if not token:
            log.warning(
                "subscribe_trade_ticks: cannot recover token_id from %s — "
                "skipping market subscribe",
                iid,
            )
            return
        log.info(
            "PolymarketDataClient subscribe trade_ticks → market subscribe",
            token=token[:16],
        )
        self._ws.subscribe_market([token])

    def _token_for_instrument(self, instrument_id) -> str:
        """
        Recover the full Polymarket token_id from an InstrumentId.

        Strategies build instruments via `parquet_loader.make_instrument`
        which puts a 1-line `<short>-<event_id>-0.0` string in the
        Symbol. The runner pushes the full 77-digit token via
        `register_tokens(short → full)` before the node starts; we look
        it up here.
        """
        short = instrument_id.symbol.value
        full = self._symbol_to_token.get(short)
        if full is None:
            log.warning(
                "no full token for instrument %s (map has %d entries)",
                instrument_id, len(self._symbol_to_token),
            )
            return ""
        return full

    def register_tokens(self, mapping: dict[str, str]) -> None:
        """Called by the runner before node start with {short_symbol: full_token}."""
        self._symbol_to_token.update(mapping)

    async def _subscribe_quote_ticks(self, command) -> None:
        log.debug(
            "PolymarketDataClient subscribe quote_ticks (no-op for PM market WS)",
            instrument_id=str(command.instrument_id),
        )

    async def _unsubscribe_order_book_deltas(self, command) -> None:
        log.debug("unsubscribe book deltas", instrument_id=str(command.instrument_id))

    async def _unsubscribe_trade_ticks(self, command) -> None:
        log.debug("unsubscribe trade_ticks", instrument_id=str(command.instrument_id))

    async def _disconnect(self) -> None:
        log.info("PolymarketDataClient disconnecting")
        await self._ws.stop()
        await self._rest.close()

    # ------------------------------------------------------------------
    # WebSocket message handler
    # ------------------------------------------------------------------

    _ws_msg_count = 0

    def _handle_ws_message(self, msg: Any) -> None:
        # PM sends arrays of events on its market channel — unwrap.
        if isinstance(msg, list):
            for item in msg:
                self._handle_ws_message(item)
            return
        if not isinstance(msg, dict):
            log.debug("Unhandled WS message (non-dict)", msg_type=type(msg).__name__)
            return
        self._ws_msg_count += 1
        if self._ws_msg_count <= 3 or self._ws_msg_count % 100 == 0:
            log.info(
                "PM ws msg #%d type=%s keys=%s",
                self._ws_msg_count, msg.get("event_type"), list(msg.keys())[:6],
            )
        event_type = msg.get("event_type") or msg.get("type")
        if event_type in ("book", "price_change"):
            self._handle_book_event(msg)
        elif event_type == "last_trade_price":
            self._handle_trade_event(msg)
        else:
            log.debug("Unhandled WS message type", event_type=event_type)

    def _instrument_for_token(self, token_id: str, condition_id: str = ""):
        """
        Look up an InstrumentId matching the convention used by strategies.

        Strategies build InstrumentIds via `parquet_loader.make_instrument`
        (BettingInstrument format: `<short>-<event_id>-0.0.POLYMARKET`).
        The data client receives raw token_ids from the WS — we have to
        synthesise the same InstrumentId so the publish matches what
        subscribers asked for.
        """
        from trading_lab.data.parquet_loader import make_instrument

        # No condition_id from WS payload — pass an empty string; the
        # BettingInstrument event_id derives from token_id alone in that
        # case (hash-based).
        instr = make_instrument(token_id, condition_id or "")
        return instr.id, instr

    def _handle_book_event(self, msg: dict[str, Any]) -> None:
        """
        Parse a Polymarket market-channel `book` or `price_change` event
        and publish `OrderBookDeltas`.

        Real WS shape (book event):
            {
              "event_type": "book",
              "asset_id":   "<token>",
              "market":     "<condition>",
              "bids":       [{"price": "0.5", "size": "100"}, ...],
              "asks":       [{"price": "0.6", "size": "80"}, ...],
              "last_trade_price": "0.55",
              "timestamp":  "1700000000000",
            }
        price_change events have NO top-level asset_id — only inner entries
        in `price_changes[]` with their own asset_id.
        """
        event_type = msg.get("event_type") or msg.get("type")
        try:
            ts_event = int(msg.get("timestamp") or 0) * 1_000_000
        except (TypeError, ValueError):
            ts_event = self._clock.timestamp_ns()
        if ts_event <= 0:
            ts_event = self._clock.timestamp_ns()
        ts_init = self._clock.timestamp_ns()

        if event_type == "book":
            asset_id = msg.get("asset_id") or ""
            condition_id = msg.get("market") or ""
            if not asset_id:
                return
            iid, instr = self._instrument_for_token(asset_id, condition_id)
            deltas: list[OrderBookDelta] = [
                OrderBookDelta.clear(iid, 0, ts_event, ts_init),
            ]
            for bid in msg.get("bids", []) or []:
                lvl = _parse_book_level(bid, OrderSide.BUY, instr)
                if lvl is None:
                    continue
                deltas.append(
                    OrderBookDelta(
                        instrument_id=iid,
                        action=BookAction.ADD,
                        order=lvl,
                        flags=0,
                        sequence=0,
                        ts_event=ts_event,
                        ts_init=ts_init,
                    )
                )
            for ask in msg.get("asks", []) or []:
                lvl = _parse_book_level(ask, OrderSide.SELL, instr)
                if lvl is None:
                    continue
                deltas.append(
                    OrderBookDelta(
                        instrument_id=iid,
                        action=BookAction.ADD,
                        order=lvl,
                        flags=0,
                        sequence=0,
                        ts_event=ts_event,
                        ts_init=ts_init,
                    )
                )
            if len(deltas) > 1:
                self._handle_data(
                    OrderBookDeltas(instrument_id=iid, deltas=deltas)
                )
            # Also publish a synthetic TradeTick for last_trade_price so
            # trade-tick-subscribing strategies get a signal.
            ltp = msg.get("last_trade_price")
            if ltp:
                self._handle_trade_event({
                    "asset_id": asset_id,
                    "price": ltp,
                    "timestamp": msg.get("timestamp"),
                    "side": "",
                })
            return

        if event_type == "price_change":
            for entry in msg.get("price_changes", []) or []:
                asset_id = entry.get("asset_id") or ""
                if not asset_id:
                    continue
                iid, instr = self._instrument_for_token(asset_id)
                lvl = _parse_book_level(
                    entry,
                    OrderSide.BUY if entry.get("side") == "BUY" else OrderSide.SELL,
                    instr,
                )
                if lvl is None:
                    continue
                deltas = [
                    OrderBookDelta(
                        instrument_id=iid,
                        action=BookAction.UPDATE,
                        order=lvl,
                        flags=0,
                        sequence=0,
                        ts_event=ts_event,
                        ts_init=ts_init,
                    ),
                ]
                self._handle_data(
                    OrderBookDeltas(instrument_id=iid, deltas=deltas)
                )
                # Trade-tick path: feed the changed level's price as a
                # synthetic trade.
                self._handle_trade_event({
                    "asset_id": asset_id,
                    "price": entry.get("price"),
                    "timestamp": msg.get("timestamp"),
                    "side": entry.get("side", ""),
                })
            return

    def _handle_trade_event(self, msg: dict[str, Any]) -> None:
        """
        Publish a `TradeTick` derived from a `last_trade_price`-style message.

        Polymarket's market channel sends `book` events with a
        `last_trade_price` field and `price_change` events without trade
        prints. Both can be coerced into a synthetic `TradeTick` so that
        trade-tick-subscribing strategies receive a price-change signal.

        Size is rarely present on these messages — default to 1.0 share
        when missing rather than dropping the event, since the strategy
        only cares about price flow.
        """
        from nautilus_trader.model.data import TradeTick
        from nautilus_trader.model.enums import AggressorSide
        from nautilus_trader.model.identifiers import TradeId

        asset_id = msg.get("asset_id") or msg.get("market") or ""
        if not asset_id:
            return
        try:
            price = float(
                msg.get("price") or msg.get("last_trade_price") or 0
            )
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        price = max(0.01, min(0.99, round(price, 2)))
        try:
            size = float(msg.get("size") or 1.0)
        except (TypeError, ValueError):
            size = 1.0

        side_str = (msg.get("side") or "").upper()
        aggressor = (
            AggressorSide.BUYER if side_str == "BUY"
            else AggressorSide.SELLER if side_str == "SELL"
            else AggressorSide.NO_AGGRESSOR
        )
        try:
            ts_ms = int(msg.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts_ms = self._clock.timestamp_ns() // 1_000_000
        ts_ns = ts_ms * 1_000_000

        raw_id = str(msg.get("trade_id") or msg.get("hash") or f"T{ts_ms}")
        tid = raw_id[2:34] if raw_id.startswith("0x") else raw_id[:32]
        iid, instr = self._instrument_for_token(asset_id, msg.get("market") or "")
        tick = TradeTick(
            instrument_id=iid,
            price=instr.make_price(price),
            size=instr.make_qty(max(size, 0.01)),
            aggressor_side=aggressor,
            trade_id=TradeId(tid or f"T{ts_ms}"),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        self._handle_data(tick)


def _parse_book_level(level: dict[str, Any], side: OrderSide, instrument=None):
    """
    Convert a raw {"price": "0.55", "size": "100"} dict to a BookOrder.

    When `instrument` is provided, prices/sizes are clamped to the
    instrument's precision grid (PM tick = 0.01, qty precision = 2).
    """
    from nautilus_trader.model.data import BookOrder

    try:
        px = float(level.get("price", 0))
        sz = float(level.get("size", 0))
    except (TypeError, ValueError):
        return None
    if px <= 0 or sz <= 0:
        return None
    px = max(0.01, min(0.99, round(px, 2)))
    if instrument is not None:
        price = instrument.make_price(px)
        qty = instrument.make_qty(sz)
    else:
        price = Price.from_str(f"{px:.2f}")
        qty = Quantity.from_str(f"{sz:.2f}")
    return BookOrder(
        side=side,
        price=price,
        size=qty,
        order_id=0,
    )
