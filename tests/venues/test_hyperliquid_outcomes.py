from __future__ import annotations

import asyncio

from trading_lab.venues.hyperliquid.client import HyperliquidRestClient
from trading_lab.venues.hyperliquid.outcomes import (
    OUTCOME_ASSET_OFFSET,
    decode_outcome_encoding,
    encode_outcome_side,
    make_hl_outcome_instrument,
    outcome_asset_id,
    outcome_coin,
    parse_outcome_coin,
    parse_outcome_meta,
)

_DUMMY_KEY = "0x" + "11" * 32

_SAMPLE_META = {
    "outcomes": [
        {
            "outcome": 100,
            "name": "Fallback",
            "description": "fallback",
            "sideSpecs": [{"name": "Yes"}, {"name": "No"}],
            "quoteToken": "USDC",
        },
        {
            "outcome": 101,
            "name": "Above 4.3%",
            "description": "sample",
            "sideSpecs": [{"name": "Yes"}, {"name": "No"}],
            "quoteToken": "USDC",
        },
    ],
    "questions": [
        {
            "question": 19,
            "name": "May CPI year-over-year",
            "description": "desc",
            "fallbackOutcome": 100,
            "namedOutcomes": [101],
            "settledNamedOutcomes": [],
        }
    ],
}


def test_outcome_encoding_roundtrip() -> None:
    encoding = encode_outcome_side(101, 1)
    assert encoding == 1011
    assert decode_outcome_encoding(encoding) == (101, 1)
    assert outcome_coin(101, 0) == "#1010"
    assert parse_outcome_coin("#1011") == (101, 1)
    assert outcome_asset_id(101, 0) == OUTCOME_ASSET_OFFSET + 1010


def test_parse_outcome_meta_and_instrument_builder() -> None:
    universe = parse_outcome_meta(_SAMPLE_META)
    question = universe.question_for_outcome(101)
    assert question is not None
    assert question.name == "May CPI year-over-year"
    spec = universe.outcome(101)
    instrument = make_hl_outcome_instrument(universe, 101, 0)
    assert spec.side_name(1) == "No"
    assert str(instrument.min_notional).endswith("USDC")
    assert "#1010" in instrument.selection_name
    assert instrument.market_type == "BINARY"


def test_hyperliquid_rest_coin_to_asset_supports_outcomes_and_spot() -> None:
    async def _run() -> None:
        client = HyperliquidRestClient(http_url="https://example.com", private_key=_DUMMY_KEY)
        try:
            assert await client._coin_to_asset_index("#1010") == OUTCOME_ASSET_OFFSET + 1010
            assert await client._coin_to_asset_index("@107") == 10107
            client._meta_cache = {"universe": [{"name": "BTC"}, {"name": "ETH"}]}
            assert await client._coin_to_asset_index("ETH") == 1
        finally:
            await client.close()

    asyncio.run(_run())
