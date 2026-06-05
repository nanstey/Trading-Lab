from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from nautilus_trader.config import ImportableActorConfig, ImportableStrategyConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.config import LiveExecEngineConfig
from nautilus_trader.model.identifiers import TraderId

from trading_lab.data.parquet_loader import make_instrument
from trading_lab.research.cross_venue import CrossVenueSpec
from trading_lab.venues.hyperliquid.factory import HyperliquidExecClientConfig
from trading_lab.venues.hyperliquid.factory import HyperliquidDataClientConfig
from trading_lab.venues.hyperliquid.instruments import make_hl_perpetual
from trading_lab.venues.polymarket.factory import PolymarketExecClientConfig
from trading_lab.venues.polymarket.factory import PolymarketDataClientConfig


@dataclass(frozen=True)
class CrossVenueObservePlan:
    slug: str
    spec: CrossVenueSpec
    node_config: TradingNodeConfig
    duration_secs: int
    polymarket_token_ids: list[str]
    hyperliquid_symbol: str
    instrument_ids: list[str]

    @property
    def instrument_count(self) -> int:
        return len(self.instrument_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "duration_secs": self.duration_secs,
            "instrument_count": self.instrument_count,
            "instrument_ids": self.instrument_ids,
            "polymarket_token_ids": self.polymarket_token_ids,
            "hyperliquid_symbol": self.hyperliquid_symbol,
            "spec_path": self.spec.source_path,
            "subscriptions": {
                "polymarket": list(self.polymarket_token_ids),
                "hyperliquid": [self.hyperliquid_symbol] if self.hyperliquid_symbol else [],
            },
            "node_clients": {
                "data": sorted(self.node_config.data_clients.keys()),
                "exec": sorted(self.node_config.exec_clients.keys()),
            },
        }


@dataclass(frozen=True)
class CrossVenueObserveSessionSummary:
    slug: str
    duration_secs: float
    instrument_ids: list[str]
    started: bool
    stopped: bool
    polymarket_token_ids: list[str]
    hyperliquid_symbol: str

    @property
    def instrument_count(self) -> int:
        return len(self.instrument_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "duration_secs": self.duration_secs,
            "instrument_count": self.instrument_count,
            "instrument_ids": list(self.instrument_ids),
            "started": self.started,
            "stopped": self.stopped,
            "subscriptions": {
                "polymarket": list(self.polymarket_token_ids),
                "hyperliquid": [self.hyperliquid_symbol] if self.hyperliquid_symbol else [],
            },
        }


class CrossVenueObserveRunner:
    """Prepare or run a bounded observe-only dual-venue paper session."""

    def __init__(self, *, config, spec: CrossVenueSpec, duration_secs: int = 300) -> None:
        self._config = config
        self._spec = spec
        self._duration_secs = duration_secs

    def build_plan(self) -> CrossVenueObservePlan:
        node_config = build_cross_venue_observe_node_config(config=self._config, spec=self._spec)
        instruments = self._build_instruments()
        return CrossVenueObservePlan(
            slug=self._spec.slug,
            spec=self._spec,
            node_config=node_config,
            duration_secs=self._duration_secs,
            polymarket_token_ids=[self._spec.polymarket.yes_token_id, self._spec.polymarket.no_token_id],
            hyperliquid_symbol=self._spec.hyperliquid.symbol or "",
            instrument_ids=[str(instr.id) for instr in instruments],
        )

    def run(self, *, node=None) -> CrossVenueObserveSessionSummary:
        plan = self.build_plan()
        instruments = self._build_instruments()
        node = node or self._make_node(plan.node_config)
        node.add_data_client_factory("POLYMARKET", self._polymarket_data_client_factory())
        node.add_data_client_factory("HYPERLIQUID", self._hyperliquid_data_client_factory())
        node.build()

        pm_data_client = self._find_data_client(node, "POLYMARKET")
        if pm_data_client is not None and hasattr(pm_data_client, "register_tokens"):
            pm_data_client.register_tokens({token_id: token_id for token_id in plan.polymarket_token_ids})

        strategy = self._find_strategy(node, "CrossVenueObserveStrategy")
        for instrument in instruments:
            try:
                node.cache.add_instrument(instrument)
            except Exception:
                pass
            if strategy is not None and hasattr(strategy, "register_instrument"):
                strategy.register_instrument(instrument.id)

        started = False
        stopped = False
        start_ts = time.monotonic()
        if self._duration_secs and self._duration_secs > 0:
            def _timer() -> None:
                nonlocal stopped
                time.sleep(self._duration_secs)
                try:
                    node.stop()
                    stopped = True
                except Exception:
                    pass

            threading.Thread(target=_timer, daemon=True, name="cross-venue-observe-timer").start()

        try:
            node.run()
            started = True
        except KeyboardInterrupt:
            try:
                node.stop()
                stopped = True
            except Exception:
                pass
        except Exception:
            pass

        return CrossVenueObserveSessionSummary(
            slug=plan.slug,
            duration_secs=round(time.monotonic() - start_ts, 2),
            instrument_ids=list(plan.instrument_ids),
            started=started,
            stopped=stopped,
            polymarket_token_ids=list(plan.polymarket_token_ids),
            hyperliquid_symbol=plan.hyperliquid_symbol,
        )

    def _build_instruments(self) -> list[Any]:
        pm_yes = make_instrument(self._spec.polymarket.yes_token_id, self._spec.polymarket.condition_id)
        pm_no = make_instrument(self._spec.polymarket.no_token_id, self._spec.polymarket.condition_id)
        instruments: list[Any] = [pm_yes, pm_no]
        hl_symbol = self._spec.hyperliquid.symbol or ""
        if hl_symbol:
            instruments.append(make_hl_perpetual(hl_symbol))
        return instruments

    def _make_node(self, node_config: TradingNodeConfig):
        from nautilus_trader.live.node import TradingNode

        return TradingNode(config=node_config)

    def _polymarket_data_client_factory(self):
        from trading_lab.venues.polymarket.factory import PolymarketLiveDataClientFactory

        return PolymarketLiveDataClientFactory

    def _hyperliquid_data_client_factory(self):
        from trading_lab.venues.hyperliquid.factory import HyperliquidLiveDataClientFactory

        return HyperliquidLiveDataClientFactory

    def _find_strategy(self, node, class_name: str):
        try:
            strategies = list(node.trader.strategies())
        except Exception:
            return None
        for strategy in strategies:
            if type(strategy).__name__ == class_name:
                return strategy
        if len(strategies) == 1:
            return strategies[0]
        return None

    def _find_data_client(self, node, venue_str: str):
        if hasattr(node, "data_clients"):
            return node.data_clients.get(venue_str)
        try:
            from nautilus_trader.model.identifiers import ClientId

            return node.kernel.data_engine._clients[ClientId(venue_str)]  # type: ignore[attr-defined]
        except Exception:
            return None



def _secret_str(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")



def build_cross_venue_strategy_config(spec: CrossVenueSpec) -> ImportableStrategyConfig:
    return ImportableStrategyConfig(
        strategy_path="trading_lab.strategies.cross_venue_observe:CrossVenueObserveStrategy",
        config_path="trading_lab.strategies.cross_venue_observe:CrossVenueObserveConfig",
        config={
            "observe_only": True,
            "poly_condition_id": spec.polymarket.condition_id,
            "poly_yes_token_id": spec.polymarket.yes_token_id,
            "poly_no_token_id": spec.polymarket.no_token_id,
            "hl_symbol": spec.hyperliquid.symbol or "",
            "hl_network": spec.hyperliquid.network,
        },
    )



def build_cross_venue_paper_strategy_config(spec: CrossVenueSpec) -> ImportableStrategyConfig:
    return ImportableStrategyConfig(
        strategy_path="trading_lab.strategies.cross_venue_hedge:CrossVenueHedgeStrategy",
        config_path="trading_lab.strategies.cross_venue_hedge:CrossVenueHedgeConfig",
        config={
            "observe_only": False,
            "poly_condition_id": spec.polymarket.condition_id,
            "poly_yes_token_id": spec.polymarket.yes_token_id,
            "poly_no_token_id": spec.polymarket.no_token_id,
            "hl_symbol": spec.hyperliquid.symbol or "",
            "hl_network": spec.hyperliquid.network,
            "fair_value_anchor_price": getattr(spec.fair_value_model, "anchor_price", 0.0),
            "fair_value_scale": getattr(spec.fair_value_model, "scale", 0.0),
            "fair_value_bias": getattr(spec.fair_value_model, "bias", 0.0),
        },
    )



def build_cross_venue_observe_node_config(*, config, spec: CrossVenueSpec) -> TradingNodeConfig:
    if spec.hyperliquid.kind != "perp":
        raise NotImplementedError("Hyperliquid outcome runtime is not integrated")

    hl_network = config.venues.hyperliquid.active(spec.hyperliquid.network)
    return TradingNodeConfig(
        trader_id=TraderId(f"XVOBS-{spec.slug[:16].upper()}"),
        logging=LoggingConfig(log_level=getattr(config, "log_level", "INFO")),
        data_clients={
            "POLYMARKET": PolymarketDataClientConfig(
                http_url=config.polymarket.host,
                api_key=config.polymarket.api_key,
                api_secret=_secret_str(config.polymarket.api_secret),
                api_passphrase=_secret_str(config.polymarket.api_passphrase),
            ),
            "HYPERLIQUID": HyperliquidDataClientConfig(
                http_url=hl_network.api_url,
                ws_url=hl_network.ws_url,
                private_key=getattr(config.hyperliquid_secrets, "network_private_key")(spec.hyperliquid.network) or "",
                account_address=getattr(config.hyperliquid_secrets, "network_account_address")(spec.hyperliquid.network) or "",
            ),
        },
        exec_clients={},
        strategies=[build_cross_venue_strategy_config(spec)],
        timeout_connection=30.0,
    )



def build_cross_venue_paper_node_config(*, config, spec: CrossVenueSpec) -> TradingNodeConfig:
    if spec.hyperliquid.kind != "perp":
        raise NotImplementedError("Hyperliquid outcome runtime is not integrated")

    hl_network = config.venues.hyperliquid.active(spec.hyperliquid.network)
    return TradingNodeConfig(
        trader_id=TraderId(f"XVPAPER-{spec.slug[:15].upper()}"),
        logging=LoggingConfig(log_level=getattr(config, "log_level", "INFO")),
        exec_engine=LiveExecEngineConfig(reconciliation=False),
        data_clients={
            "POLYMARKET": PolymarketDataClientConfig(
                http_url=config.polymarket.host,
                api_key=config.polymarket.api_key,
                api_secret=_secret_str(config.polymarket.api_secret),
                api_passphrase=_secret_str(config.polymarket.api_passphrase),
            ),
            "HYPERLIQUID": HyperliquidDataClientConfig(
                http_url=hl_network.api_url,
                ws_url=hl_network.ws_url,
                private_key=getattr(config.hyperliquid_secrets, "network_private_key")(spec.hyperliquid.network) or "",
                account_address=getattr(config.hyperliquid_secrets, "network_account_address")(spec.hyperliquid.network) or "",
            ),
        },
        exec_clients={
            "POLYMARKET": PolymarketExecClientConfig(
                http_url=config.polymarket.host,
                private_key=_secret_str(config.polymarket.private_key),
                api_key=config.polymarket.api_key,
                api_secret=_secret_str(config.polymarket.api_secret),
                api_passphrase=_secret_str(config.polymarket.api_passphrase),
                exchange_address=config.polymarket.exchange_address,
                is_paper=True,
            ),
            "HYPERLIQUID": HyperliquidExecClientConfig(
                http_url=hl_network.api_url,
                ws_url=hl_network.ws_url,
                private_key=getattr(config.hyperliquid_secrets, "network_private_key")(spec.hyperliquid.network) or "",
                account_address=getattr(config.hyperliquid_secrets, "network_account_address")(spec.hyperliquid.network) or "",
                is_paper=True,
            ),
        },
        actors=[
            ImportableActorConfig(
                actor_path="trading_lab.venues.polymarket.paper_fill:PolymarketPaperFillEngine",
                config_path="trading_lab.venues.polymarket.paper_fill:PolymarketPaperFillConfig",
                config={
                    "component_id": "POLYMARKET-PAPER-FILL",
                    "ioc_max_book_updates": 1,
                    "account_currency": "USDC",
                },
            ),
            ImportableActorConfig(
                actor_path="trading_lab.venues.hyperliquid.paper_fill:HyperliquidPaperFillEngine",
                config_path="trading_lab.venues.hyperliquid.paper_fill:HyperliquidPaperFillConfig",
                config={
                    "component_id": "HYPERLIQUID-PAPER-FILL",
                    "ioc_max_book_updates": 1,
                    "account_currency": "USDC",
                },
            ),
        ],
        strategies=[build_cross_venue_paper_strategy_config(spec)],
        timeout_connection=30.0,
    )



def build_cross_venue_observe_plan(*, config, spec: CrossVenueSpec, duration_secs: int = 300) -> CrossVenueObservePlan:
    return CrossVenueObserveRunner(config=config, spec=spec, duration_secs=duration_secs).build_plan()
