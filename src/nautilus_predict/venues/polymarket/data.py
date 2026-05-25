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
from nautilus_trader.model.enums import BookAction, OrderSide, RecordFlag
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from nautilus_predict.venues.polymarket.client import PolymarketRestClient, PolymarketWsClient

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
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(VENUE),
            venue=None,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        self._rest = client
        self._ws = ws_client

    async def _connect(self) -> None:
        log.info("PolymarketDataClient connecting")
        self._ws.start()

    async def _disconnect(self) -> None:
        log.info("PolymarketDataClient disconnecting")
        await self._ws.stop()
        await self._rest.close()

    # ------------------------------------------------------------------
    # WebSocket message handler
    # ------------------------------------------------------------------

    def _handle_ws_message(self, msg: dict[str, Any]) -> None:
        event_type = msg.get("event_type") or msg.get("type")
        if event_type in ("book", "price_change"):
            self._handle_book_event(msg)
        elif event_type == "last_trade_price":
            self._handle_trade_event(msg)
        else:
            log.debug("Unhandled WS message type", event_type=event_type)

    def _handle_book_event(self, msg: dict[str, Any]) -> None:
        """Parse orderbook snapshot or delta and publish OrderBookDeltas."""
        asset_id = msg.get("asset_id", "")
        instrument_id = InstrumentId.from_str(f"{asset_id}.{VENUE}")
        ts_event = self._clock.timestamp_ns()
        ts_init = ts_event

        deltas: list[OrderBookDelta] = []
        for bid in msg.get("buys", []):
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument_id,
                    action=BookAction.UPDATE,
                    order=_parse_book_level(bid, OrderSide.BUY),
                    flags=RecordFlag.F_LAST if not deltas else 0,
                    sequence=0,
                    ts_event=ts_event,
                    ts_init=ts_init,
                )
            )
        for ask in msg.get("sells", []):
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument_id,
                    action=BookAction.UPDATE,
                    order=_parse_book_level(ask, OrderSide.SELL),
                    flags=RecordFlag.F_LAST,
                    sequence=0,
                    ts_event=ts_event,
                    ts_init=ts_init,
                )
            )

        if deltas:
            self._handle_data(OrderBookDeltas(instrument_id=instrument_id, deltas=deltas))

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
        instrument_id = InstrumentId.from_str(f"{asset_id}.{VENUE}")
        # NT requires Price/Quantity precision from an instrument; without
        # one in cache we fall back to from_str.
        tick = TradeTick(
            instrument_id=instrument_id,
            price=Price.from_str(f"{price:.2f}"),
            size=Quantity.from_str(f"{size:.2f}"),
            aggressor_side=aggressor,
            trade_id=TradeId(tid or f"T{ts_ms}"),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        self._handle_data(tick)


def _parse_book_level(level: dict[str, Any], side: OrderSide):
    """Convert a raw {"price": "0.55", "size": "100"} dict to a BookOrder."""
    from nautilus_trader.model.book import BookOrder

    return BookOrder(
        side=side,
        price=Price.from_str(level["price"]),
        size=Quantity.from_str(level["size"]),
        order_id=0,
    )
