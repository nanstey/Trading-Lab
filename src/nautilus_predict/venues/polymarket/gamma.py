"""
Gamma API client — public market-metadata endpoint for Polymarket.

The CLOB `/markets` endpoint returns only `condition_id / question / tokens`,
which isn't enough to do strategy-aware market selection (we need volume,
liquidity, end date, category, event grouping). Gamma exposes the richer
view that the Polymarket UI itself reads.

Public, no auth required.
"""

from __future__ import annotations

from typing import Any

import aiohttp


class GammaClient:
    """Async client for `gamma-api.polymarket.com`."""

    def __init__(
        self,
        base_url: str = "https://gamma-api.polymarket.com",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._owned_session = session is None
        self._session = session or aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=16),
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def close(self) -> None:
        if self._owned_session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> GammaClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get_markets(
        self,
        active: bool | None = None,
        closed: bool | None = None,
        archived: bool | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str | None = None,
        ascending: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List markets with the given filters. Returns up to `limit` rows."""
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        if archived is not None:
            params["archived"] = str(archived).lower()
        if order is not None:
            params["order"] = order
        if ascending is not None:
            params["ascending"] = str(ascending).lower()

        async with self._session.get(f"{self._base}/markets", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if not isinstance(data, list):
                return []
            return data

    async def get_market(self, condition_id: str) -> dict[str, Any] | None:
        """Fetch a single market by condition_id."""
        async with self._session.get(
            f"{self._base}/markets", params={"condition_ids": condition_id}
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if isinstance(data, list) and data:
                return data[0]
            return None

    async def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        closed: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List events (which group related markets)."""
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if closed is not None:
            params["closed"] = str(closed).lower()
        async with self._session.get(f"{self._base}/events", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if not isinstance(data, list):
                return []
            return data

    async def iter_markets(
        self,
        active: bool | None = None,
        closed: bool | None = None,
        archived: bool | None = None,
        page_size: int = 500,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Paginate all markets matching the filter — convenience helper."""
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            page = await self.get_markets(
                active=active,
                closed=closed,
                archived=archived,
                limit=page_size,
                offset=offset,
            )
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows
