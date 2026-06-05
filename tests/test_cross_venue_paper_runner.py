from __future__ import annotations

from pathlib import Path

import pytest

from trading_lab.research.cross_venue import CrossVenueSpec, HyperliquidLeg, PolymarketLeg
from trading_lab.runner.cross_venue_paper import (
    build_cross_venue_paper_node_config,
    build_cross_venue_paper_strategy_config,
    build_cross_venue_strategy_config,
)


class _Cfg:
    class _PM:
        host = "https://clob.polymarket.com"
        api_key = "k"
        api_secret = type("S", (), {"get_secret_value": lambda self: "s"})()
        api_passphrase = type("S", (), {"get_secret_value": lambda self: "p"})()
        private_key = type("S", (), {"get_secret_value": lambda self: "0x" + "11" * 32})()
        exchange_address = "0xabc"

    class _VenueEndpoint:
        api_url = "https://api.hyperliquid.xyz"
        ws_url = "wss://api.hyperliquid.xyz/ws"

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


def _perp_spec() -> CrossVenueSpec:
    return CrossVenueSpec(
        slug="hl-pm-btc-basis",
        venue="cross_venue",
        polymarket=PolymarketLeg(condition_id="0xabc", yes_token_id="111", no_token_id="222"),
        hyperliquid=HyperliquidLeg(kind="perp", network="mainnet", symbol="BTC"),
        source_path="/tmp/spec.md",
    )


def _outcome_spec() -> CrossVenueSpec:
    return CrossVenueSpec(
        slug="hl-pm-election",
        venue="cross_venue",
        polymarket=PolymarketLeg(condition_id="0xdef", yes_token_id="333", no_token_id="444"),
        hyperliquid=HyperliquidLeg(kind="outcome", network="mainnet", outcome_id=1010, side=1),
        source_path="/tmp/spec.md",
    )


def test_build_cross_venue_strategy_config_is_observe_only() -> None:
    strategy_cfg = build_cross_venue_strategy_config(_perp_spec())

    assert strategy_cfg.strategy_path == "trading_lab.strategies.cross_venue_observe:CrossVenueObserveStrategy"
    assert strategy_cfg.config_path == "trading_lab.strategies.cross_venue_observe:CrossVenueObserveConfig"
    assert strategy_cfg.config["observe_only"] is True
    assert strategy_cfg.config["hl_symbol"] == "BTC"
    assert strategy_cfg.config["poly_yes_token_id"] == "111"


def test_build_cross_venue_paper_strategy_config_uses_hedge_strategy() -> None:
    strategy_cfg = build_cross_venue_paper_strategy_config(_perp_spec())

    assert strategy_cfg.strategy_path == "trading_lab.strategies.cross_venue_hedge:CrossVenueHedgeStrategy"
    assert strategy_cfg.config_path == "trading_lab.strategies.cross_venue_hedge:CrossVenueHedgeConfig"
    assert strategy_cfg.config["observe_only"] is False
    assert strategy_cfg.config["hl_symbol"] == "BTC"
    assert strategy_cfg.config["poly_yes_token_id"] == "111"


def test_build_cross_venue_paper_node_config_includes_both_data_and_exec_clients() -> None:
    node_cfg = build_cross_venue_paper_node_config(config=_Cfg(), spec=_perp_spec())

    assert set(node_cfg.data_clients.keys()) == {"POLYMARKET", "HYPERLIQUID"}
    assert set(node_cfg.exec_clients.keys()) == {"POLYMARKET", "HYPERLIQUID"}
    assert len(node_cfg.actors) == 2
    assert node_cfg.actors[0].actor_path.endswith("PolymarketPaperFillEngine")
    assert node_cfg.actors[1].actor_path.endswith("HyperliquidPaperFillEngine")
    assert len(node_cfg.strategies) == 1
    assert node_cfg.timeout_connection == 30.0


def test_build_cross_venue_paper_node_config_refuses_hl_outcomes_for_now() -> None:
    with pytest.raises(NotImplementedError, match="Hyperliquid outcome runtime is not integrated"):
        build_cross_venue_paper_node_config(config=_Cfg(), spec=_outcome_spec())
