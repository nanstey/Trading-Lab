from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from trading_lab.research.cross_venue import (
    HyperliquidLeg,
    PolymarketLeg,
    load_cross_venue_spec,
    validate_cross_venue_spec,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_cross_venue_readiness = _load_script_module(
    "check_cross_venue_readiness",
    "scripts/check_cross_venue_readiness.py",
)


VALID_PERP_SPEC = """---
slug: hl-pm-btc-basis
venue: cross_venue
cross_venue:
  polymarket:
    condition_id: 0xabc
    yes_token_id: 111
    no_token_id: 222
  hyperliquid:
    kind: perp
    symbol: BTC
    network: mainnet
strategy_module: trading_lab.strategies.cross_venue_hedge
strategy_class: CrossVenueHedgeStrategy
strategy_config_class: CrossVenueHedgeConfig
---

# HL / PM basis
"""


VALID_OUTCOME_SPEC = """---
slug: hl-pm-election-spread
venue: cross_venue
cross_venue:
  polymarket:
    condition_id: 0xdef
    yes_token_id: 333
    no_token_id: 444
  hyperliquid:
    kind: outcome
    outcome_id: 1010
    side: 1
    network: mainnet
---
"""


INVALID_SPEC = """---
slug: broken-cross-venue
venue: cross_venue
cross_venue:
  polymarket:
    condition_id: 0xbroken
    yes_token_id: 555
  hyperliquid:
    kind: perp
---
"""


def test_load_cross_venue_spec_parses_perp_leg(tmp_path: Path) -> None:
    path = tmp_path / "spec.md"
    path.write_text(VALID_PERP_SPEC)

    spec = load_cross_venue_spec(path)

    assert spec.slug == "hl-pm-btc-basis"
    assert spec.polymarket == PolymarketLeg(
        condition_id="0xabc",
        yes_token_id="111",
        no_token_id="222",
    )
    assert spec.hyperliquid == HyperliquidLeg(
        kind="perp",
        network="mainnet",
        symbol="BTC",
        outcome_id=None,
        side=None,
    )


def test_validate_cross_venue_spec_rejects_missing_leg_fields(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text(INVALID_SPEC)

    spec = load_cross_venue_spec(path)
    errors = validate_cross_venue_spec(spec)

    assert "cross_venue.polymarket.no_token_id is required" in errors
    assert "cross_venue.hyperliquid.symbol is required when kind=perp" in errors


def test_readiness_report_distinguishes_perp_vs_outcome_support(tmp_path: Path) -> None:
    perp_path = tmp_path / "perp.md"
    perp_path.write_text(VALID_PERP_SPEC)
    outcome_path = tmp_path / "outcome.md"
    outcome_path.write_text(VALID_OUTCOME_SPEC)

    perp = check_cross_venue_readiness.build_readiness_report(perp_path)
    outcome = check_cross_venue_readiness.build_readiness_report(outcome_path)

    assert perp["ok"] is True
    assert perp["spec"]["hyperliquid"]["kind"] == "perp"
    assert perp["readiness"]["develop"] is True
    assert perp["readiness"]["backtest"] is False
    assert perp["readiness"]["paper_trade"] is False
    assert "dual_venue_backtest_runner_missing" in perp["gaps"]
    assert "dual_venue_paper_runner_missing" in perp["gaps"]

    assert outcome["ok"] is True
    assert outcome["spec"]["hyperliquid"]["kind"] == "outcome"
    assert "hyperliquid_outcome_runtime_not_integrated" in outcome["gaps"]


def test_readiness_report_serializes_as_json(tmp_path: Path) -> None:
    path = tmp_path / "spec.md"
    path.write_text(VALID_PERP_SPEC)

    report = check_cross_venue_readiness.build_readiness_report(path)
    encoded = json.dumps(report)

    assert '"slug": "hl-pm-btc-basis"' in encoded
