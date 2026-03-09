"""
Polymarket Data Ingestion.

Handles both historical (REST-based) and real-time (WebSocket-based)
data collection, writing all data to the Parquet catalog.

Two modes:
1. Historical batch ingestion: Fetches paginated REST history
2. Continuous streaming: Subscribes to WebSocket feeds

Used by scripts/download_polymarket_data.py for one-off data pulls
and by the paper/live runners for real-time data archival.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nautilus_predict.adapters.polymarket.client import PolymarketClient
    from nautilus_predict.data.catalog import DataCatalog

log = logging.getLogger(__name__)


class PolymarketDataIngester:
    """
    Fetches and stores Polymarket market data.

    Supports historical data pulls via REST and real-time streaming
    via WebSocket. All data is persisted to the DataCatalog.

    Parameters
    ----------
    client : PolymarketClient
        Authenticated (or public) Polymarket client.
    catalog : DataCatalog
        Parquet data catalog for storage.

    Example
    -------
    >>> ingester = PolymarketDataIngester(client=poly_client, catalog=catalog)
    >>> await ingester.fetch_historical_trades(
    ...     token_id="0xabc",
    ...     start_ts=1700000000,
    ...     end_ts=1700086400,
    ... )
    """

    def __init__(self, client: PolymarketClient, catalog: DataCatalog) -> None:
        self._client = client
        self._catalog = catalog

    async def fetch_historical_trades(
        self,
        token_id: str,
        start_ts: int,
        end_ts: int,
        page_size: int = 500,
    ) -> int:
        """
        Fetch historical trade records via REST and write to catalog.

        Paginates through all available trades in the given time window
        and writes them to the Parquet catalog in batches.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.
        start_ts : int
            Start Unix timestamp (seconds).
        end_ts : int
            End Unix timestamp (seconds).
        page_size : int
            Number of trades to fetch per page.

        Returns
        -------
        int
            Total number of trades fetched and stored.

        TODO(live): Confirm Polymarket trade history endpoint path
        TODO(live): Handle API rate limiting with exponential backoff
        """
        total_trades = 0
        cursor: str | None = None

        log.info(
            "Fetching historical trades",
            extra={
                "token_id": token_id,
                "start": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            },
        )

        while True:
            # TODO(live): Call actual trade history endpoint
            # The Polymarket API endpoint for historical trades is:
            # GET /trades?asset_id={token_id}&after={start_ts}&before={end_ts}
            # Currently stubbed - wire to actual endpoint when confirmed
            params: dict[str, Any] = {
                "asset_id": token_id,
                "after": start_ts * 1000,
                "before": end_ts * 1000,
                "limit": page_size,
            }
            if cursor:
                params["next_cursor"] = cursor

            try:
                # TODO(live): Replace with actual client method
                # response = await self._client.get_trades(**params)
                log.warning(
                    "Historical trade fetch not yet implemented",
                    extra={"token_id": token_id},
                )
                break

            except Exception as exc:
                log.error(
                    "Error fetching trades page",
                    extra={"token_id": token_id, "cursor": cursor, "error": str(exc)},
                )
                break

        return total_trades

    async def fetch_historical_orderbook_snapshots(
        self,
        token_id: str,
        start_ts: int,
        end_ts: int,
        interval_secs: int = 60,
    ) -> int:
        """
        Fetch periodic order book snapshots for a time range.

        Polls the REST order book endpoint at regular intervals to
        build a historical record of book state.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.
        start_ts : int
            Start Unix timestamp (seconds).
        end_ts : int
            End Unix timestamp (seconds).
        interval_secs : int
            Interval between snapshots in seconds (default: 60).

        Returns
        -------
        int
            Total number of snapshots stored.

        TODO(live): The CLOB API may not support historical book snapshots.
        Consider recording real-time book state via subscribe_market() instead.
        TODO(live): Evaluate if Polymarket provides CLOB history endpoints.
        """
        log.warning(
            "Historical orderbook snapshots may not be supported by Polymarket API",
            extra={"token_id": token_id},
        )
        total_snapshots = 0

        current_ts = start_ts
        while current_ts <= end_ts:
            try:
                book_data = await self._client.get_order_book(token_id=token_id)

                bids = [
                    (float(level["price"]), float(level["size"]))
                    for level in book_data.get("bids", [])
                ]
                asks = [
                    (float(level["price"]), float(level["size"]))
                    for level in book_data.get("asks", [])
                ]

                self._catalog.write_orderbook_snapshot(
                    token_id=token_id,
                    timestamp=current_ts * 1000,
                    bids=bids,
                    asks=asks,
                )
                total_snapshots += 1

                if current_ts + interval_secs > end_ts:
                    break

                # Rate limiting: be polite to the API
                await asyncio.sleep(0.5)
                current_ts += interval_secs

            except Exception as exc:
                log.error(
                    "Error fetching orderbook snapshot",
                    extra={"token_id": token_id, "timestamp": current_ts, "error": str(exc)},
                )
                await asyncio.sleep(5.0)
                current_ts += interval_secs

        log.info(
            "Orderbook snapshot fetch complete",
            extra={"token_id": token_id, "total_snapshots": total_snapshots},
        )
        return total_snapshots

    async def run_continuous(self, token_ids: list[str]) -> None:
        """
        Subscribe to WebSocket feeds and stream data to the catalog.

        Subscribes to market channels for all provided token IDs and
        writes incoming order book updates to the Parquet catalog.
        Runs indefinitely until cancelled.

        Parameters
        ----------
        token_ids : list[str]
            List of token IDs to subscribe to.

        TODO(live): Handle multiple simultaneous WebSocket connections
        TODO(live): Implement graceful restart on connection errors
        """
        log.info(
            "Starting continuous data ingestion",
            extra={"token_count": len(token_ids), "token_ids": token_ids[:5]},
        )

        tasks = [
            asyncio.create_task(
                self._client.subscribe_market(token_id, self._on_market_message),
                name=f"ingest-{token_id[:8]}",
            )
            for token_id in token_ids
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            log.info("Continuous ingestion stopped")

    async def _on_market_message(self, message: dict[str, Any]) -> None:
        """
        Handle incoming WebSocket market messages and persist to catalog.

        Parameters
        ----------
        message : dict
            Raw WebSocket message from Polymarket market channel.
        """
        msg_type = message.get("event_type", "")

        if msg_type in ("book", "price_change"):
            token_id = message.get("asset_id", "")
            if not token_id:
                return

            timestamp = int(message.get("timestamp", 0))
            bids = [
                (float(b["price"]), float(b["size"]))
                for b in message.get("bids", [])
            ]
            asks = [
                (float(a["price"]), float(a["size"]))
                for a in message.get("asks", [])
            ]

            self._catalog.write_orderbook_snapshot(
                token_id=token_id,
                timestamp=timestamp,
                bids=bids,
                asks=asks,
            )

        elif msg_type == "trade":
            token_id = message.get("asset_id", "")
            if token_id:
                self._catalog.write_trades(token_id, [message])
