"""
Hyperliquid historical data client.

Thin wrapper over the public `/info` endpoint for the three queries we need
to build a research catalog:

  * `candleSnapshot`    — OHLCV bars for one coin/interval/window.
                          HL caps each response at 5000 rows, so we paginate
                          by walking `startTime` forward until we hit `end`.
  * `fundingHistory`    — hourly funding rate per coin. Same pagination idea
                          (no documented per-request cap but we chunk by 30
                          days to keep responses tidy and retryable).
  * `metaAndAssetCtxs`  — current universe + per-asset 24h notional volume,
                          open interest, mark price, funding. Used by the
                          monthly universe snapshot.

This client is intentionally signing-free: no private key is needed because
all calls go to `/info`. We open our own aiohttp session so it can be used
in scripts without standing up the full execution client.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import aiohttp
import structlog

log = structlog.get_logger(__name__)

# HL's documented per-response cap. Empirically also applies to fundingHistory.
MAX_CANDLES_PER_REQUEST = 5000

# Bar duration in milliseconds for the intervals we care about.
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

# Funding pays hourly on HL. Use a 30-day chunk for funding queries.
FUNDING_CHUNK_MS = 30 * 86_400_000


class HyperliquidHistoricalClient:
    """Read-only client for HL historical info endpoints."""

    def __init__(
        self,
        http_url: str,
        session: aiohttp.ClientSession | None = None,
        request_timeout_s: float = 60.0,
        retry_attempts: int = 8,
        retry_base_delay_s: float = 1.5,
    ) -> None:
        self._base = http_url.rstrip("/")
        self._owned_session = session is None
        self._session: aiohttp.ClientSession = session or aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=4),
            timeout=aiohttp.ClientTimeout(total=request_timeout_s),
        )
        self._retry_attempts = retry_attempts
        self._retry_base_delay_s = retry_base_delay_s

    async def close(self) -> None:
        if self._owned_session:
            await self._session.close()

    async def __aenter__(self) -> HyperliquidHistoricalClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Universe / metadata
    # ------------------------------------------------------------------

    async def get_meta(self) -> dict[str, Any]:
        return await self._info({"type": "meta"})

    async def get_meta_and_asset_ctxs(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """
        Return (meta, asset_ctxs). `asset_ctxs[i]` corresponds to
        `meta["universe"][i]` — same index space.
        """
        resp = await self._info({"type": "metaAndAssetCtxs"})
        if not isinstance(resp, list) or len(resp) != 2:
            raise ValueError(f"Unexpected metaAndAssetCtxs response shape: {type(resp)}")
        meta, ctxs = resp
        return meta, ctxs

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------

    async def fetch_candles(
        self,
        coin: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch all candles for `coin` at `interval` between `[start_ms, end_ms]`.

        Handles the 5000-row per-response cap by walking the cursor forward.
        Returns raw HL dicts: `{T, c, h, i, l, n, o, s, t, v}` (strings for
        OHLCV; ints for timestamps and trade count).
        """
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval {interval!r}; choose from {list(INTERVAL_MS)}")
        if end_ms <= start_ms:
            return []

        bar_ms = INTERVAL_MS[interval]
        # One request can cover at most this many ms of bars.
        max_span_ms = bar_ms * MAX_CANDLES_PER_REQUEST

        out: list[dict[str, Any]] = []
        cursor = start_ms
        seen_keys: set[int] = set()

        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + max_span_ms)
            batch = await self._info(
                {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": coin,
                        "interval": interval,
                        "startTime": cursor,
                        "endTime": chunk_end,
                    },
                }
            )
            if not isinstance(batch, list):
                raise ValueError(f"Unexpected candleSnapshot response for {coin}: {type(batch)}")
            if not batch:
                # No data in this slice — jump forward by the span and continue.
                cursor = chunk_end + bar_ms
                continue

            # Dedupe on bar open-time (cursor walks can overlap by one bar).
            kept = 0
            for c in batch:
                t = int(c["t"])
                if t in seen_keys:
                    continue
                seen_keys.add(t)
                out.append(c)
                kept += 1

            last_t = int(batch[-1]["t"])
            log.debug(
                "hl_candles_chunk",
                coin=coin,
                interval=interval,
                cursor_iso=_iso(cursor),
                last_iso=_iso(last_t),
                got=len(batch),
                kept=kept,
            )

            # Advance cursor to one bar past the last seen open.
            next_cursor = last_t + bar_ms
            if next_cursor <= cursor:
                # Server returned only stale rows; bail to avoid infinite loop.
                cursor = chunk_end + bar_ms
            else:
                cursor = next_cursor

        out.sort(key=lambda c: int(c["t"]))
        return out

    # ------------------------------------------------------------------
    # Funding
    # ------------------------------------------------------------------

    async def fetch_funding_history(
        self,
        coin: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch all funding entries for `coin` between `[start_ms, end_ms]`.

        Returns raw dicts: `{coin, fundingRate, premium, time}`.
        """
        if end_ms <= start_ms:
            return []

        out: list[dict[str, Any]] = []
        cursor = start_ms
        seen_keys: set[int] = set()
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + FUNDING_CHUNK_MS)
            batch = await self._info(
                {
                    "type": "fundingHistory",
                    "coin": coin,
                    "startTime": cursor,
                    "endTime": chunk_end,
                }
            )
            if not isinstance(batch, list):
                raise ValueError(f"Unexpected fundingHistory response for {coin}: {type(batch)}")

            if batch:
                for f in batch:
                    t = int(f["time"])
                    if t in seen_keys:
                        continue
                    seen_keys.add(t)
                    out.append(f)
                last_t = int(batch[-1]["time"])
                # Funding is hourly; +1h to step past the last seen entry.
                next_cursor = last_t + 3_600_000
                if next_cursor <= cursor:
                    cursor = chunk_end + 3_600_000
                else:
                    cursor = next_cursor
            else:
                cursor = chunk_end + 3_600_000

        out.sort(key=lambda f: int(f["time"]))
        return out

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _info(self, payload: dict[str, Any]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                async with self._session.post(
                    f"{self._base}/info",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 429:
                        # Rate limited — back off and retry.
                        delay = self._retry_base_delay_s * (2**attempt)
                        log.warning("hl_rate_limit", delay_s=delay, attempt=attempt)
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                delay = self._retry_base_delay_s * (2**attempt)
                log.warning(
                    "hl_info_retry",
                    error=str(exc),
                    attempt=attempt,
                    delay_s=delay,
                    payload_type=payload.get("type"),
                )
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"HL /info request failed after {self._retry_attempts} attempts: {last_exc}"
        )


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()
