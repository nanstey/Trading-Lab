from __future__ import annotations

from typing import Any

from nautilus_trader.config import ImportableStrategyConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.model.identifiers import TraderId

from trading_lab.research.cross_venue import CrossVenueSpec
from trading_lab.venues.hyperliquid.factory import HyperliquidDataClientConfig
from trading_lab.venues.polymarket.factory import PolymarketDataClientConfig


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
