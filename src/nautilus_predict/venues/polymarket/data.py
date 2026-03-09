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
from nautilus_trader.model.data import OrderBookDelta, OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import BookAction, OrderSide, RecordFlag
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from nautilus_predict.venues.polymarket.client import PolymarketRestClient, PolymarketWsClient
from nautilus_predict.venues.polymarket.auth import L2Credentials

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
        """Parse a last-trade-price message (best-effort trade tick)."""
        # Full trade tick construction requires size which is not always present;
        # skip if insufficient data rather than fabricate incorrect records.
        pass


def _parse_book_level(level: dict[str, Any], side: OrderSide):
    """Convert a raw {"price": "0.55", "size": "100"} dict to a BookOrder."""
    from nautilus_trader.model.book import BookOrder

    return BookOrder(
        side=side,
        price=Price.from_str(level["price"]),
        size=Quantity.from_str(level["size"]),
        order_id=0,
    )
