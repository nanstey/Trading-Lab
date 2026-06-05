from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from trading_lab.research.cross_venue import CrossVenueSpec, HyperliquidLeg, PolymarketLeg
from trading_lab.runner.cross_venue_paper import build_cross_venue_strategy_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cross_venue_paper_run = _load_script_module(
    "cross_venue_paper_run",
    "scripts/cross_venue_paper_run.py",
)


VALID_SPEC = """---
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
    network: testnet
strategy_module: trading_lab.strategies.cross_venue_observe
strategy_class: CrossVenueObserveStrategy
strategy_config_class: CrossVenueObserveConfig
---

# observe-only cross venue
"""


class _Cfg:
    class _PM:
        host = "https://clob.polymarket.com"
        api_key = "k"
        api_secret = type("S", (), {"get_secret_value": lambda self: "s"})()
        api_passphrase = type("S", (), {"get_secret_value": lambda self: "p"})()

    class _VenueEndpoint:
        api_url = "https://api.hyperliquid-testnet.xyz"
        ws_url = "wss://api.hyperliquid-testnet.xyz/ws"

    class _HLSecrets:
        def network_private_key(self, network: str) -> str:
            return ""

        def network_account_address(self, network: str) -> str:
            return ""

    class _Venues:
        class _HL:
            @staticmethod
            def active(network: str):
                return _Cfg._VenueEndpoint()

        hyperliquid = _HL()

    polymarket = _PM()
    hyperliquid_secrets = _HLSecrets()
    venues = _Venues()
    log_level = "INFO"


def test_resolve_cross_venue_hypothesis_path_prefers_folder_spec(tmp_path: Path) -> None:
    hypotheses_dir = tmp_path / "research" / "hypotheses"
    spec_dir = hypotheses_dir / "hl-pm-btc-basis"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "spec.md"
    spec_path.write_text(VALID_SPEC)

    resolved = cross_venue_paper_run.resolve_cross_venue_hypothesis_path(
        "hl-pm-btc-basis",
        hypotheses_dir=hypotheses_dir,
    )

    assert resolved == spec_path


def test_build_observe_run_plan_reports_three_instruments(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(VALID_SPEC)

    plan = cross_venue_paper_run.build_observe_run_plan(spec_path, config=_Cfg(), duration_secs=90)

    assert plan.slug == "hl-pm-btc-basis"
    assert plan.duration_secs == 90
    assert plan.instrument_count == 3
    assert plan.hyperliquid_symbol == "BTC"
    assert plan.polymarket_token_ids == ["111", "222"]
    assert plan.node_config.data_clients.keys() == {"POLYMARKET", "HYPERLIQUID"}
    assert build_cross_venue_strategy_config(plan.spec).config["observe_only"] is True


def test_build_dry_run_report_is_json_serializable(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(VALID_SPEC)

    report = cross_venue_paper_run.build_dry_run_report(spec_path, config=_Cfg(), duration_secs=45)
    encoded = json.dumps(report)

    assert report["ok"] is True
    assert report["mode"] == "dry_run"
    assert report["instrument_count"] == 3
    assert report["subscriptions"]["hyperliquid"] == ["BTC"]
    assert '"spec_path"' in encoded
