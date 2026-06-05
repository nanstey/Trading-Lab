"""Thin interaction adapter for Hyperliquid outcome markets.

This module intentionally stays one layer above the shared REST/WS transport.
It normalizes `outcomeMeta`, coin/asset encoding, and common interaction flows
without claiming that Nautilus paper/live integration is complete.
"""

from __future__ import annotations

from typing import Any

from trading_lab.venues.hyperliquid.client import HyperliquidRestClient
from trading_lab.venues.hyperliquid.outcomes import (
    OutcomeQuestion,
    OutcomeSpec,
    OutcomeUniverse,
    outcome_asset_id,
    outcome_coin,
    parse_outcome_meta,
)


class HyperliquidOutcomeClient:
    """Convenience wrapper around Hyperliquid outcome-market metadata + order flow."""

    def __init__(self, rest: HyperliquidRestClient) -> None:
        self._rest = rest
        self._universe_cache: OutcomeUniverse | None = None

    async def get_universe(self, *, refresh: bool = False) -> OutcomeUniverse:
        if refresh or self._universe_cache is None:
            payload = await self._rest.get_outcome_meta()
            self._universe_cache = parse_outcome_meta(payload)
        return self._universe_cache

    async def list_questions(self, *, refresh: bool = False) -> list[OutcomeQuestion]:
        universe = await self.get_universe(refresh=refresh)
        return list(universe.questions.values())

    async def list_outcomes(self, *, refresh: bool = False, include_fallback: bool = False) -> list[OutcomeSpec]:
        universe = await self.get_universe(refresh=refresh)
        if include_fallback:
            return list(universe.outcomes.values())
        return universe.named_outcomes()

    async def get_orderbook(self, outcome_id: int, side: int) -> dict[str, Any]:
        return await self._rest.get_orderbook(outcome_coin(outcome_id, side))

    async def place_order(
        self,
        outcome_id: int,
        side: int,
        *,
        is_buy: bool,
        price: float,
        size: float,
        order_type: dict[str, Any] | None = None,
        cloid: str | None = None,
    ) -> dict[str, Any]:
        return await self._rest.place_order_asset(
            asset=outcome_asset_id(outcome_id, side),
            is_buy=is_buy,
            price=price,
            size=size,
            order_type=order_type,
            reduce_only=False,
            cloid=cloid,
        )

    async def cancel_order(self, outcome_id: int, side: int, order_id: int) -> dict[str, Any]:
        return await self._rest.cancel_order_asset(
            asset=outcome_asset_id(outcome_id, side),
            order_id=order_id,
        )

    async def question_for_outcome(self, outcome_id: int, *, refresh: bool = False) -> OutcomeQuestion | None:
        universe = await self.get_universe(refresh=refresh)
        return universe.question_for_outcome(outcome_id)
