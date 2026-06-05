from __future__ import annotations

from pathlib import Path

import pytest

from trading_lab.research.cross_venue import CrossVenueSpec, FairValueModelSpec, HyperliquidLeg, PolymarketLeg
from trading_lab.runner.cross_venue_paper import (
    CrossVenuePaperRunner,
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


class _FakeDataClient:
    def __init__(self) -> None:
        self.token_map = None

    def register_tokens(self, token_map):
        self.token_map = dict(token_map)


class _FakeExecClient:
    def __init__(self) -> None:
        self._paper_fill_engine = None


class _FakeActor:
    def __init__(self, component_id: str) -> None:
        self.id = component_id
        self.registered = []

    def register_instrument(self, instrument_id) -> None:
        self.registered.append(str(instrument_id))


class _FakeStrategy:
    def __init__(self) -> None:
        self.cross_venue_legs = None

    def register_cross_venue_legs(self, **legs) -> None:
        self.cross_venue_legs = {k: str(v) for k, v in legs.items()}

    def register_instrument(self, instrument_id) -> None:
        pass


class _FakeCache:
    def __init__(self) -> None:
        self.instruments = []

    def add_instrument(self, instrument) -> None:
        self.instruments.append(str(instrument.id))


class _FakeTrader:
    def __init__(self, strategy, actors) -> None:
        self._strategy = strategy
        self._actors = actors

    def strategies(self):
        return [self._strategy]

    def actors(self):
        return list(self._actors)


class _FakeNode:
    def __init__(self, config) -> None:
        self.config = config
        self.cache = _FakeCache()
        self.strategy = _FakeStrategy()
        self.actors_list = [_FakeActor("POLYMARKET-PAPER-FILL"), _FakeActor("HYPERLIQUID-PAPER-FILL")]
        self.trader = _FakeTrader(self.strategy, self.actors_list)
        self.data_clients = {"POLYMARKET": _FakeDataClient(), "HYPERLIQUID": object()}
        self.exec_clients = {"POLYMARKET": _FakeExecClient(), "HYPERLIQUID": _FakeExecClient()}
        self.added_data_factories = []
        self.added_exec_factories = []
        self.built = False
        self.ran = False
        self.stopped = False

    def add_data_client_factory(self, venue: str, factory) -> None:
        self.added_data_factories.append(venue)

    def add_exec_client_factory(self, venue: str, factory) -> None:
        self.added_exec_factories.append(venue)

    def build(self) -> None:
        self.built = True

    def run(self) -> None:
        self.ran = True

    def stop(self) -> None:
        self.stopped = True


def _perp_spec() -> CrossVenueSpec:
    return CrossVenueSpec(
        slug="hl-pm-btc-basis",
        venue="cross_venue",
        polymarket=PolymarketLeg(condition_id="0xabc", yes_token_id="111", no_token_id="222"),
        hyperliquid=HyperliquidLeg(kind="perp", network="mainnet", symbol="BTC"),
        fair_value_model=FairValueModelSpec(anchor_price=65000.0, scale=2500.0, bias=0.0),
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


def test_cross_venue_paper_runner_wires_fill_engines_and_leg_registration() -> None:
    runner = CrossVenuePaperRunner(config=_Cfg(), spec=_perp_spec(), duration_secs=0)
    node = _FakeNode(build_cross_venue_paper_node_config(config=_Cfg(), spec=_perp_spec()))

    summary = runner.run(node=node)

    assert summary.started is True
    assert summary.instrument_count == 3
    assert set(node.added_data_factories) == {"POLYMARKET", "HYPERLIQUID"}
    assert set(node.added_exec_factories) == {"POLYMARKET", "HYPERLIQUID"}
    assert node.exec_clients["POLYMARKET"]._paper_fill_engine is node.actors_list[0]
    assert node.exec_clients["HYPERLIQUID"]._paper_fill_engine is node.actors_list[1]
    assert len(node.actors_list[0].registered) == 2
    assert len(node.actors_list[1].registered) == 1
    assert node.strategy.cross_venue_legs is not None
    assert node.data_clients["POLYMARKET"].token_map == {"111": "111", "222": "222"}
