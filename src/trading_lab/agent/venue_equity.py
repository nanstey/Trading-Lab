"""
Venue equity provider — total account capital on a venue.

Allocator uses this to resolve percentage-of-equity caps. The provider is
intentionally venue-aware (not strategy-aware) so all strategies running
against the same Polymarket wallet see the same total when computing their
percentage slice. That is the whole point of the abstraction: when one
strategy makes money the others' caps grow proportionally; when the wallet
shrinks everyone's caps shrink together.

For Polymarket the equity is:
    free USDC (CLOB balance/allowance endpoint)
  + value of open positions (data-api /value endpoint)
  + notional of resting orders (sum of qty*price across open orders)

We fall back gracefully: if `/value` fails we use balance + open-order
notional; if even that fails we return the cached last-good value and
log a warning. A stuck provider is better than a halted trading system —
the operator can refresh via `scripts/portfolio_status.py --refresh`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

POLYMARKET_DATA_API_DEFAULT = "https://data-api.polymarket.com"


@dataclass
class EquitySnapshot:
    """One read of the venue equity, with provenance for events / debugging."""

    venue: str
    total_usdc: float
    free_usdc: float
    open_position_value_usdc: float
    open_order_notional_usdc: float
    source: str  # "data_api" | "clob+orders" | "cached" | "fallback"
    ts: float  # unix time of read


class PolymarketEquityProvider:
    """
    Reads total Polymarket wallet equity. Cached; refresh on demand.

    Construction is cheap (no I/O). Call `await refresh()` to populate.
    `current_usdc()` returns the cached value (0.0 until first refresh).

    Args:
        wallet_address:  the user's Polygon address (derived from POLY_PRIVATE_KEY).
        rest_client:     `PolymarketRestClient` (used for /balance + /orders).
        data_api_url:    PM data-api root (default https://data-api.polymarket.com).
        session:         optional aiohttp ClientSession; one is created if absent.
    """

    def __init__(
        self,
        wallet_address: str,
        rest_client: Any | None = None,
        data_api_url: str = POLYMARKET_DATA_API_DEFAULT,
        session: Any | None = None,
    ) -> None:
        self._wallet = wallet_address
        self._rest = rest_client
        self._data_api = data_api_url.rstrip("/")
        self._session = session
        self._snapshot: EquitySnapshot | None = None

    # ------------------------------------------------------------------
    # Synchronous accessors
    # ------------------------------------------------------------------

    def current_usdc(self) -> float:
        """Cached total equity in USDC. 0.0 until first successful refresh."""
        return self._snapshot.total_usdc if self._snapshot else 0.0

    @property
    def snapshot(self) -> EquitySnapshot | None:
        return self._snapshot

    def age_seconds(self) -> float | None:
        if self._snapshot is None:
            return None
        return max(0.0, time.time() - self._snapshot.ts)

    # ------------------------------------------------------------------
    # Async refresh — call at startup and (optionally) on a timer
    # ------------------------------------------------------------------

    async def refresh(self) -> EquitySnapshot:
        """
        Pull live equity from the venue. Caches into `self._snapshot`.

        Order of preference:
          1. data-api `/value?user=<addr>` — single call, includes open
             positions valued at last trade. Returned `value` is USDC.
          2. Fallback: free USDC balance from CLOB + sum of qty*price for
             open orders. Misses unrealised PnL on open positions but is a
             safe lower bound for capacity planning.
          3. If both fail, return the previous snapshot tagged source=cached
             (or a zero-snapshot tagged source=fallback if there's no prior).
        """
        try:
            snap = await self._refresh_via_data_api()
            self._snapshot = snap
            return snap
        except Exception as exc:
            log.warning("equity: data-api refresh failed: %s", exc)

        try:
            snap = await self._refresh_via_clob()
            self._snapshot = snap
            return snap
        except Exception as exc:
            log.warning("equity: clob+orders refresh failed: %s", exc)

        if self._snapshot is not None:
            self._snapshot = EquitySnapshot(
                **{**self._snapshot.__dict__, "source": "cached", "ts": self._snapshot.ts}
            )
            return self._snapshot

        self._snapshot = EquitySnapshot(
            venue="POLYMARKET",
            total_usdc=0.0,
            free_usdc=0.0,
            open_position_value_usdc=0.0,
            open_order_notional_usdc=0.0,
            source="fallback",
            ts=time.time(),
        )
        return self._snapshot

    # ------------------------------------------------------------------
    # Refresh strategies
    # ------------------------------------------------------------------

    async def _refresh_via_data_api(self) -> EquitySnapshot:
        """
        GET data-api.polymarket.com/value?user=<addr> → {"value": <usdc>, ...}

        We don't ship a long-lived aiohttp session in this module — create
        one per refresh. Refreshes are rare (startup + periodic) so the
        overhead is negligible.
        """
        if not self._wallet:
            raise RuntimeError("no wallet address configured")
        url = f"{self._data_api}/value"
        params = {"user": self._wallet}
        import aiohttp

        sess = self._session or aiohttp.ClientSession()
        try:
            async with sess.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                body = await resp.json()
        finally:
            if self._session is None:
                await sess.close()

        # data-api returns either a list of {user, value} or a single dict.
        value = 0.0
        if isinstance(body, list) and body:
            value = float(body[0].get("value", 0) or 0)
        elif isinstance(body, dict):
            value = float(body.get("value", 0) or 0)
        if value <= 0:
            raise RuntimeError(f"data-api returned non-positive value: {body!r}")

        return EquitySnapshot(
            venue="POLYMARKET",
            total_usdc=value,
            free_usdc=0.0,
            open_position_value_usdc=value,
            open_order_notional_usdc=0.0,
            source="data_api",
            ts=time.time(),
        )

    async def _refresh_via_clob(self) -> EquitySnapshot:
        """
        Fallback: CLOB `/balance-allowance` for free USDC, plus
        `/data/orders` (open orders) for the resting-order notional.

        Note: this misses the mark-to-market value of open POSITIONS
        (only counts cash + orders). The allocator treats this as a
        conservative lower bound — strategies see less headroom than
        they actually have, which is the safe direction.
        """
        if self._rest is None:
            raise RuntimeError("no rest_client configured for fallback")

        free = 0.0
        try:
            bal = await self._rest._get(
                "/balance-allowance", params={"asset_type": "COLLATERAL"}, auth=True
            )
            if isinstance(bal, dict):
                free = float(bal.get("balance", 0) or 0)
        except Exception as exc:
            log.debug("equity: balance-allowance failed: %s", exc)

        open_order_notional = 0.0
        try:
            orders = await self._rest._get("/data/orders", auth=True)
            for o in orders or []:
                try:
                    open_order_notional += float(o.get("size", 0)) * float(
                        o.get("price", 0)
                    )
                except Exception:
                    pass
        except Exception as exc:
            log.debug("equity: orders fetch failed: %s", exc)

        total = free + open_order_notional
        if total <= 0:
            raise RuntimeError("clob fallback returned zero equity")
        return EquitySnapshot(
            venue="POLYMARKET",
            total_usdc=total,
            free_usdc=free,
            open_position_value_usdc=0.0,
            open_order_notional_usdc=open_order_notional,
            source="clob+orders",
            ts=time.time(),
        )


# ---------------------------------------------------------------------------
# Static / test helpers
# ---------------------------------------------------------------------------


class StaticEquityProvider:
    """
    Equity provider with a hard-coded value. For tests and for offline runs
    where you want pct-of-equity semantics without hitting the venue.
    """

    def __init__(self, total_usdc: float, venue: str = "POLYMARKET") -> None:
        self._snapshot = EquitySnapshot(
            venue=venue,
            total_usdc=float(total_usdc),
            free_usdc=float(total_usdc),
            open_position_value_usdc=0.0,
            open_order_notional_usdc=0.0,
            source="static",
            ts=time.time(),
        )

    def current_usdc(self) -> float:
        return self._snapshot.total_usdc

    @property
    def snapshot(self) -> EquitySnapshot:
        return self._snapshot

    def age_seconds(self) -> float | None:
        return 0.0

    async def refresh(self) -> EquitySnapshot:
        self._snapshot = EquitySnapshot(
            **{**self._snapshot.__dict__, "ts": time.time()}
        )
        return self._snapshot

    def set_value(self, new_value: float) -> None:
        """Mutate the static value — useful for tests of shrink/grow."""
        self._snapshot = EquitySnapshot(
            **{**self._snapshot.__dict__, "total_usdc": float(new_value), "ts": time.time()}
        )
