from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nautilus_trader.config import ImportableStrategyConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.model.identifiers import TraderId

from trading_lab.data.parquet_loader import make_instrument
from trading_lab.research.cross_venue import CrossVenueSpec
from trading_lab.venues.hyperliquid.factory import HyperliquidDataClientConfig
from trading_lab.venues.hyperliquid.instruments import make_hl_perpetual
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


class CrossVenueObserveRunner:
    """Prepare a bounded observe-only dual-venue paper session."""

    def __init__(self, *, config, spec: CrossVenueSpec, duration_secs: int = 300) -> None:
        self._config = config
        self._spec = spec
        self._duration_secs = duration_secs

    def build_plan(self) -> CrossVenueObservePlan:
        node_config = build_cross_venue_paper_node_config(config=self._config, spec=self._spec)
        pm_yes = make_instrument(self._spec.polymarket.yes_token_id, self._spec.polymarket.condition_id)
        pm_no = make_instrument(self._spec.polymarket.no_token_id, self._spec.polymarket.condition_id)
        hl_symbol = self._spec.hyperliquid.symbol or ""
        hl_instr = make_hl_perpetual(hl_symbol) if hl_symbol else None
        instrument_ids = [str(pm_yes.id), str(pm_no.id)]
        if hl_instr is not None:
            instrument_ids.append(str(hl_instr.id))
        return CrossVenueObservePlan(
            slug=self._spec.slug,
            spec=self._spec,
            node_config=node_config,
            duration_secs=self._duration_secs,
            polymarket_token_ids=[self._spec.polymarket.yes_token_id, self._spec.polymarket.no_token_id],
            hyperliquid_symbol=hl_symbol,
            instrument_ids=instrument_ids,
        )



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



def build_cross_venue_paper_node_config(*, config, spec: CrossVenueSpec) -> TradingNodeConfig:
    if spec.hyperliquid.kind != "perp":
        raise NotImplementedError("Hyperliquid outcome runtime is not integrated")

    hl_network = config.venues.hyperliquid.active(spec.hyperliquid.network)
    return TradingNodeConfig(
        trader_id=TraderId(f"XVPAPER-{spec.slug[:15].upper()}"),
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



def build_cross_venue_observe_plan(*, config, spec: CrossVenueSpec, duration_secs: int = 300) -> CrossVenueObservePlan:
    return CrossVenueObserveRunner(config=config, spec=spec, duration_secs=duration_secs).build_plan()
