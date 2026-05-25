"""
Polymarket Data Ingestion.

Historical and real-time market data collection. Writes everything to the
Parquet DataCatalog so downstream backtests and analyses have a single source
of truth.

Two flows:

1. **Historical batch** — `fetch_historical_trades(condition_id, ...)` paginates
   `data-api.polymarket.com/trades?market=<conditionId>` with offset paging.
   That endpoint returns trades for BOTH legs (YES + NO) of a binary market
   in one stream; each row has an `asset` field (token ID) so the catalog
   partitions correctly. `fetch_orderbook_snapshots(token_id, ...)` polls
   `clob.polymarket.com/book?token_id=<id>` on an interval (live-forward only
   — there's no historical-book endpoint).

2. **Continuous streaming** — `run_continuous(token_ids)` subscribes to the
   Polymarket market WS channel via `PolymarketWsClient` and persists book
   snapshots + trade prints as they arrive.

Used by `scripts/download_polymarket_data.py` for one-off pulls and by the
paper/live runners for in-process archival.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from nautilus_predict.data.catalog import DataCatalog
    from nautilus_predict.venues.polymarket.client import PolymarketRestClient

log = logging.getLogger(__name__)

DATA_API_BASE = "https://data-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


class PolymarketDataIngester:
    """
    Fetch and persist Polymarket market data.

    The ingester intentionally talks to the public data-api and CLOB book
    endpoints directly (aiohttp) rather than going through `PolymarketRestClient`
    — those endpoints don't require auth and live on different hosts.

    Parameters
    ----------
    client : PolymarketRestClient | None
        Authenticated CLOB client, used only for the live WebSocket subscription
        path. Pass None when only doing historical pulls.
    catalog : DataCatalog
        Parquet catalog for persistence.
    """

    def __init__(
        self,
        catalog: DataCatalog,
        client: PolymarketRestClient | None = None,
        request_concurrency: int = 5,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._semaphore = asyncio.Semaphore(request_concurrency)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> PolymarketDataIngester:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=32),
            timeout=aiohttp.ClientTimeout(total=30),
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=32),
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    # ------------------------------------------------------------------
    # Historical: trades (data-api, condition-scoped)
    # ------------------------------------------------------------------

    async def fetch_historical_trades(
        self,
        condition_id: str,
        start_ts: int,
        end_ts: int,
        page_size: int = 500,
    ) -> int:
        """
        Fetch historical trades for a condition (both YES + NO legs).

        Walks `GET data-api.polymarket.com/trades?market=<conditionId>` in two
        phases:

        1. **Offset phase** — newest-first paging via `offset=N`. The data-api
           rejects offsets above ~3500 with HTTP 400, so this only covers the
           most recent slice.
        2. **Before-timestamp phase** — once the offset wall is hit, switch to
           cursor-by-timestamp: `before=<oldest_ts_seen - 1>` with offset reset.
           Continues until every trade on a page predates `start_ts` or the
           response is empty.

        Together these get arbitrarily-deep history (subject to whatever the
        upstream actually retains).
        """
        session = await self._ensure_session()
        total = 0
        offset = 0
        before: int | None = None
        oldest_ts_this_run = end_ts
        log.info(
            "fetch_historical_trades start condition=%s start=%s end=%s",
            condition_id,
            datetime.fromtimestamp(start_ts, tz=UTC).isoformat(),
            datetime.fromtimestamp(end_ts, tz=UTC).isoformat(),
        )

        while True:
            params: dict[str, str] = {
                "market": condition_id,
                "limit": str(page_size),
                "offset": str(offset),
            }
            if before is not None:
                params["before"] = str(before)
            async with self._semaphore:
                try:
                    async with session.get(f"{DATA_API_BASE}/trades", params=params) as resp:
                        if resp.status == 400 and before is None:
                            # Hit offset wall in newest-first mode — switch to
                            # before-timestamp cursor.
                            log.info(
                                "offset cap at %s; switching to before=%s cursor",
                                offset, oldest_ts_this_run,
                            )
                            before = oldest_ts_this_run - 1
                            offset = 0
                            continue
                        resp.raise_for_status()
                        page = await resp.json()
                except Exception as exc:
                    log.error(
                        "trades fetch failed offset=%s before=%s err=%s",
                        offset, before, exc,
                    )
                    break

            if not isinstance(page, list) or not page:
                if before is None:
                    # Try the before-cursor phase before giving up.
                    if oldest_ts_this_run <= start_ts:
                        break
                    before = oldest_ts_this_run - 1
                    offset = 0
                    continue
                break

            by_token: dict[str, list[dict[str, Any]]] = {}
            all_older = True
            for row in page:
                ts = int(row.get("timestamp", 0))
                if ts < oldest_ts_this_run:
                    oldest_ts_this_run = ts
                if ts > end_ts:
                    continue
                if ts >= start_ts:
                    all_older = False
                else:
                    continue

                asset = str(row.get("asset", ""))
                if not asset:
                    continue
                by_token.setdefault(asset, []).append(
                    {
                        "timestamp": ts * 1000,
                        "trade_id": row.get("transactionHash", ""),
                        "side": str(row.get("side", "")),
                        "price": float(row.get("price", 0.0)),
                        "size": float(row.get("size", 0.0)),
                        "fee": 0.0,
                    }
                )

            for token, trades in by_token.items():
                self._catalog.write_trades(token, trades)
                total += len(trades)

            if total > 0 and total % 1000 == 0:
                log.info("fetch_historical_trades progress total=%s", total)

            if len(page) < page_size:
                # Reached the end of the dataset.
                break
            if all_older:
                # Every record on the page is older than start_ts.
                break

            offset += page_size

        log.info("fetch_historical_trades done condition=%s total=%s", condition_id, total)
        return total

    # ------------------------------------------------------------------
    # Historical: book snapshots (CLOB, token-scoped, polled forward)
    # ------------------------------------------------------------------

    async def fetch_orderbook_snapshots(
        self,
        token_id: str,
        start_ts: int,
        end_ts: int,
        interval_sec: int = 60,
    ) -> int:
        """
        Poll the CLOB `/book` endpoint and persist snapshots.

        Polymarket does not expose historical book data, so this is only useful
        for forward-looking captures (start_ts in the future or "now"). For
        backtests on historical data, see the reconstruction helper in
        `data/parquet_loader.py`.

        Parameters
        ----------
        token_id : str
            Outcome token ID.
        start_ts : int
            Earliest snapshot timestamp (seconds). If in the past, sleeps are
            short-circuited and we capture immediately.
        end_ts : int
            Last snapshot timestamp (seconds).
        interval_sec : int
            Polling interval.

        Returns
        -------
        int
            Snapshots persisted.
        """
        session = await self._ensure_session()
        captured = 0
        loop = asyncio.get_running_loop()

        while True:
            now = int(loop.time())  # monotonic — used only for sleep math
            wall = int(datetime.now(tz=UTC).timestamp())
            if wall > end_ts:
                break
            if wall < start_ts:
                await asyncio.sleep(min(start_ts - wall, interval_sec))
                continue

            try:
                async with self._semaphore, session.get(
                    f"{CLOB_BASE}/book", params={"token_id": token_id}
                ) as resp:
                    resp.raise_for_status()
                    book = await resp.json()
            except Exception as exc:
                log.warning("book fetch failed token=%s err=%s", token_id[:16], exc)
                await asyncio.sleep(interval_sec)
                continue

            bids = [
                (float(b["price"]), float(b["size"])) for b in book.get("bids", [])
            ]
            asks = [
                (float(a["price"]), float(a["size"])) for a in book.get("asks", [])
            ]
            ts_ms = int(book.get("timestamp", wall * 1000))
            self._catalog.write_orderbook_snapshot(token_id, ts_ms, bids, asks)
            captured += 1
            log.debug("book snapshot token=%s levels=%d", token_id[:16], len(bids) + len(asks))

            await asyncio.sleep(interval_sec)
            _ = now  # silence unused

        return captured

    # ------------------------------------------------------------------
    # Live streaming
    # ------------------------------------------------------------------

    async def run_continuous(self, token_ids: list[str]) -> None:
        """
        Subscribe to live market WS feeds for `token_ids` and persist messages.

        Requires `self._client` (a `PolymarketRestClient`) — the WS subscription
        is queued through its companion `PolymarketWsClient`.
        """
        if self._client is None:
            raise RuntimeError("run_continuous() requires a PolymarketRestClient")

        log.info("continuous ingest start tokens=%d", len(token_ids))

        from nautilus_predict.venues.polymarket.auth import L2Credentials
        from nautilus_predict.venues.polymarket.client import PolymarketWsClient

        # Reuse the REST client's L2 creds for the WS auth path (user channel).
        # Market-channel subscriptions don't strictly need auth.
        creds = getattr(self._client, "_creds", None)
        if creds is None:
            creds = L2Credentials(api_key="", api_secret="", api_passphrase="")

        ws = PolymarketWsClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            creds=creds,
            on_message=lambda m: asyncio.create_task(self._on_market_message(m)),
        )
        ws.subscribe_market(token_ids)
        await ws.connect_and_run()

    async def _on_market_message(self, message: dict[str, Any]) -> None:
        """
        Handle one inbound WS message from the market channel.

        Polymarket emits a few flavours:
          - `event_type: "book"`  — full book snapshot with bids/asks
          - `event_type: "price_change"` — incremental change (delta)
          - `event_type: "last_trade_price"` — trade print
        """
        ev = message.get("event_type", "")
        token_id = message.get("asset_id") or message.get("market") or ""
        if not token_id:
            return

        if ev == "book":
            ts = int(message.get("timestamp", 0) or 0)
            bids = [
                (float(b["price"]), float(b["size"])) for b in message.get("bids", [])
            ]
            asks = [
                (float(a["price"]), float(a["size"])) for a in message.get("asks", [])
            ]
            self._catalog.write_orderbook_snapshot(token_id, ts, bids, asks)

        elif ev == "price_change":
            # Incremental update — persist as a snapshot with just the changed side.
            ts = int(message.get("timestamp", 0) or 0)
            changes = message.get("changes", [])
            bids: list[tuple[float, float]] = []
            asks: list[tuple[float, float]] = []
            for c in changes:
                price = float(c.get("price", 0))
                size = float(c.get("size", 0))
                if c.get("side") == "BUY":
                    bids.append((price, size))
                else:
                    asks.append((price, size))
            if bids or asks:
                self._catalog.write_orderbook_snapshot(token_id, ts, bids, asks)

        elif ev in ("last_trade_price", "trade"):
            ts = int(message.get("timestamp", 0) or 0)
            self._catalog.write_trades(
                token_id,
                [
                    {
                        "timestamp": ts,
                        "trade_id": str(message.get("trade_id", "")),
                        "side": str(message.get("side", "")),
                        "price": float(message.get("price", 0)),
                        "size": float(message.get("size", 0)),
                        "fee": 0.0,
                    }
                ],
            )
