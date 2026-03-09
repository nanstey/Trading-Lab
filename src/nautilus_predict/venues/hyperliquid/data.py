"""
Hyperliquid live data client (NautilusTrader DataClient).

Streams real-time orderbook and trade data from Hyperliquid's WebSocket API
and translates it into NautilusTrader domain objects.
"""

from __future__ import annotations

from typing import Any

import structlog
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import OrderBookDelta, OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import AggressorSide, BookAction, OrderSide, RecordFlag
from nautilus_trader.model.identifiers import ClientId, InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

from nautilus_predict.venues.hyperliquid.client import HyperliquidRestClient, HyperliquidWsClient

log = structlog.get_logger(__name__)

VENUE = "HYPERLIQUID"


class HyperliquidDataClient(LiveMarketDataClient):
    """NautilusTrader data client for Hyperliquid."""

    def __init__(
        self,
        loop,
        rest: HyperliquidRestClient,
        ws: HyperliquidWsClient,
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
        self._rest = rest
        self._ws = ws

    async def _connect(self) -> None:
        log.info("HyperliquidDataClient connecting")
        self._ws.start()

    async def _disconnect(self) -> None:
        log.info("HyperliquidDataClient disconnecting")
        await self._ws.stop()
        await self._rest.close()

    def _handle_ws_message(self, msg: dict[str, Any]) -> None:
        channel = msg.get("channel")
        data = msg.get("data")
        if data is None:
            return

        if channel == "l2Book":
            self._handle_book(data)
        elif channel == "trades":
            self._handle_trades(data)

    def _handle_book(self, data: dict[str, Any]) -> None:
        coin = data.get("coin", "")
        instrument_id = InstrumentId.from_str(f"{coin}-PERP.{VENUE}")
        ts = self._clock.timestamp_ns()

        levels = data.get("levels", [[], []])
        bids_raw, asks_raw = levels[0], levels[1]

        deltas: list[OrderBookDelta] = []

        for bid in bids_raw:
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument_id,
                    action=BookAction.UPDATE,
                    order=_make_book_order(bid["px"], bid["sz"], OrderSide.BUY),
                    flags=0,
                    sequence=0,
                    ts_event=ts,
                    ts_init=ts,
                )
            )
        for ask in asks_raw:
            deltas.append(
                OrderBookDelta(
                    instrument_id=instrument_id,
                    action=BookAction.UPDATE,
                    order=_make_book_order(ask["px"], ask["sz"], OrderSide.SELL),
                    flags=RecordFlag.F_LAST,
                    sequence=0,
                    ts_event=ts,
                    ts_init=ts,
                )
            )

        if deltas:
            self._handle_data(OrderBookDeltas(instrument_id=instrument_id, deltas=deltas))

    def _handle_trades(self, trades: list[dict[str, Any]]) -> None:
        for trade in trades:
            coin = trade.get("coin", "")
            instrument_id = InstrumentId.from_str(f"{coin}-PERP.{VENUE}")
            ts = self._clock.timestamp_ns()

            side = AggressorSide.BUYER if trade.get("side") == "B" else AggressorSide.SELLER

            tick = TradeTick(
                instrument_id=instrument_id,
                price=Price.from_str(str(trade["px"])),
                size=Quantity.from_str(str(trade["sz"])),
                aggressor_side=side,
                trade_id=TradeId(str(trade.get("tid", ts))),
                ts_event=ts,
                ts_init=ts,
            )
            self._handle_data(tick)


def _make_book_order(price_str: str, size_str: str, side: OrderSide):
    from nautilus_trader.model.book import BookOrder

    return BookOrder(
        side=side,
        price=Price.from_str(price_str),
        size=Quantity.from_str(size_str),
        order_id=0,
    )
