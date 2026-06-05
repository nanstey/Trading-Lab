"""Hyperliquid outcome-market helpers and metadata models.

Outcome markets are distinct from Hyperliquid perps:
- fully collateralized, not margined perps
- separate outcome/question metadata via `outcomeMeta`
- asset IDs derived from `(outcome_id, side)` rather than `meta.universe` index
- public market data exposed via coin strings like `#1010`

This module keeps the encoding logic and metadata normalization in one place so
transport clients and future Nautilus adapters do not have to re-derive it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

import pandas as pd
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.instruments import BettingInstrument
from nautilus_trader.model.objects import Money


OUTCOME_ASSET_OFFSET = 100_000_000
VALID_OUTCOME_SIDES = (0, 1)


@dataclass(frozen=True)
class OutcomeSideSpec:
    side: int
    name: str


@dataclass(frozen=True)
class OutcomeSpec:
    outcome: int
    name: str
    description: str
    side_specs: tuple[OutcomeSideSpec, OutcomeSideSpec]
    quote_token: str = "USDC"

    def side_name(self, side: int) -> str:
        if side not in VALID_OUTCOME_SIDES:
            raise ValueError(f"Invalid outcome side: {side!r}")
        return self.side_specs[side].name

    def coin(self, side: int) -> str:
        return outcome_coin(self.outcome, side)

    def asset_id(self, side: int) -> int:
        return outcome_asset_id(self.outcome, side)


@dataclass(frozen=True)
class OutcomeQuestion:
    question: int
    name: str
    description: str
    fallback_outcome: int | None
    named_outcomes: tuple[int, ...]
    settled_named_outcomes: tuple[int, ...]


@dataclass(frozen=True)
class OutcomeUniverse:
    outcomes: dict[int, OutcomeSpec]
    questions: dict[int, OutcomeQuestion]

    def outcome(self, outcome_id: int) -> OutcomeSpec:
        try:
            return self.outcomes[outcome_id]
        except KeyError as exc:
            raise KeyError(f"Unknown Hyperliquid outcome id: {outcome_id}") from exc

    def question(self, question_id: int) -> OutcomeQuestion:
        try:
            return self.questions[question_id]
        except KeyError as exc:
            raise KeyError(f"Unknown Hyperliquid question id: {question_id}") from exc

    def question_for_outcome(self, outcome_id: int) -> OutcomeQuestion | None:
        for question in self.questions.values():
            if outcome_id == question.fallback_outcome or outcome_id in question.named_outcomes:
                return question
        return None

    def named_outcomes(self) -> list[OutcomeSpec]:
        fallback_ids = {q.fallback_outcome for q in self.questions.values() if q.fallback_outcome is not None}
        return [spec for oid, spec in self.outcomes.items() if oid not in fallback_ids]


def encode_outcome_side(outcome_id: int, side: int) -> int:
    if side not in VALID_OUTCOME_SIDES:
        raise ValueError(f"Invalid outcome side: {side!r}; expected 0 or 1")
    if outcome_id < 0:
        raise ValueError(f"Invalid outcome id: {outcome_id!r}")
    return 10 * outcome_id + side


def decode_outcome_encoding(encoding: int) -> tuple[int, int]:
    if encoding < 0:
        raise ValueError(f"Invalid outcome encoding: {encoding!r}")
    outcome_id, side = divmod(encoding, 10)
    if side not in VALID_OUTCOME_SIDES:
        raise ValueError(f"Invalid encoded side {side!r} in outcome encoding {encoding!r}")
    return outcome_id, side


def outcome_coin(outcome_id: int, side: int) -> str:
    return f"#{encode_outcome_side(outcome_id, side)}"


def outcome_token_name(outcome_id: int, side: int) -> str:
    return f"+{encode_outcome_side(outcome_id, side)}"


def outcome_asset_id(outcome_id: int, side: int) -> int:
    return OUTCOME_ASSET_OFFSET + encode_outcome_side(outcome_id, side)


def parse_outcome_coin(coin: str) -> tuple[int, int]:
    if not coin.startswith("#"):
        raise ValueError(f"Outcome coin must start with '#': {coin!r}")
    return decode_outcome_encoding(int(coin[1:]))


def parse_outcome_meta(payload: dict[str, Any]) -> OutcomeUniverse:
    outcomes: dict[int, OutcomeSpec] = {}
    for raw in payload.get("outcomes", []):
        outcome_id = int(raw["outcome"])
        raw_sides = raw.get("sideSpecs") or [{"name": "Yes"}, {"name": "No"}]
        if len(raw_sides) != 2:
            raise ValueError(f"Outcome {outcome_id} has unsupported side count: {len(raw_sides)}")
        side_specs = tuple(
            OutcomeSideSpec(side=i, name=str(side.get("name", f"Side {i}")))
            for i, side in enumerate(raw_sides)
        )
        outcomes[outcome_id] = OutcomeSpec(
            outcome=outcome_id,
            name=str(raw.get("name", f"Outcome {outcome_id}")),
            description=str(raw.get("description", "")),
            side_specs=(side_specs[0], side_specs[1]),
            quote_token=str(raw.get("quoteToken", "USDC")),
        )

    questions: dict[int, OutcomeQuestion] = {}
    for raw in payload.get("questions", []):
        question_id = int(raw["question"])
        fallback = raw.get("fallbackOutcome")
        questions[question_id] = OutcomeQuestion(
            question=question_id,
            name=str(raw.get("name", f"Question {question_id}")),
            description=str(raw.get("description", "")),
            fallback_outcome=int(fallback) if fallback is not None else None,
            named_outcomes=tuple(int(v) for v in raw.get("namedOutcomes", [])),
            settled_named_outcomes=tuple(int(v) for v in raw.get("settledNamedOutcomes", [])),
        )

    return OutcomeUniverse(outcomes=outcomes, questions=questions)


def make_hl_outcome_instrument(
    universe: OutcomeUniverse,
    outcome_id: int,
    side: int,
    *,
    price_precision: int = 5,
    size_precision: int = 2,
    min_notional_usdc: float = 10.0,
) -> BettingInstrument:
    """Build a NautilusTrader `BettingInstrument` for one Hyperliquid outcome side.

    This is a metadata scaffold for future strategy/backtest integration. It
    intentionally does not claim runner support by itself.
    """
    spec = universe.outcome(outcome_id)
    question = universe.question_for_outcome(outcome_id)

    event_name = question.name if question else f"OutcomeQuestion-{outcome_id}"
    market_name = spec.name
    selection_name = spec.side_name(side)
    market_id = f"HLO{outcome_id}"[:16]
    selection_id = _stable_int31(f"hl-outcome:{outcome_id}:{side}")
    event_id = _stable_int31(f"hl-question:{question.question if question else outcome_id}")

    return BettingInstrument(
        venue_name="HYPERLIQUID",
        betting_type="ODDS",
        competition_id=0,
        competition_name="HYPERLIQUID",
        event_country_code="US",
        event_id=event_id,
        event_name=event_name[:60],
        event_open_date=pd.Timestamp.fromtimestamp(0, tz="UTC"),
        event_type_id=1,
        event_type_name="PredictionMarket",
        market_id=market_id,
        market_name=market_name[:60],
        market_start_time=pd.Timestamp.fromtimestamp(0, tz="UTC"),
        market_type="BINARY",
        selection_handicap=0.0,
        selection_id=selection_id,
        selection_name=f"{selection_name} {outcome_coin(outcome_id, side)}"[:60],
        currency="USDC",
        price_precision=price_precision,
        size_precision=size_precision,
        min_notional=Money(min_notional_usdc, USDC),
        ts_event=0,
        ts_init=0,
    )


def _stable_int31(text: str) -> int:
    digest = hashlib.sha1(text.encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
