"""
NautilusTrader TradingNode factory.

Assembles the node with venue adapters and strategies based on the active
TradingMode. The node runs entirely in-memory; no database is required.
"""

from __future__ import annotations

from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from nautilus_predict.config import TradingMode, load_config
from nautilus_predict.strategies.arb_complement import BinaryArbStrategy, BinaryArbConfig
from nautilus_predict.strategies.market_maker import MarketMakingStrategy, MarketMakingConfig
from nautilus_predict.venues.hyperliquid.factory import HyperliquidLiveDataClientFactory
from nautilus_predict.venues.hyperliquid.factory import HyperliquidLiveExecClientFactory
from nautilus_predict.venues.polymarket.factory import PolymarketLiveDataClientFactory
from nautilus_predict.venues.polymarket.factory import PolymarketLiveExecClientFactory


def build_node(mode: TradingMode = TradingMode.PAPER) -> TradingNode:
    """Construct a fully configured TradingNode."""
    cfg = load_config()

    node_config = TradingNodeConfig(
        trader_id=TraderId("NAUTILUS-PREDICT-001"),
        logging=LoggingConfig(
            log_level=cfg.log_level,
            log_colors=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=15,
        ),
        data_clients={
            "POLYMARKET": {
                "factory": PolymarketLiveDataClientFactory,
                "config": {
                    "http_url": cfg.polymarket.http_url,
                    "ws_url": cfg.polymarket.ws_url,
                    "api_key": cfg.polymarket.api_key,
                    "api_secret": cfg.polymarket.api_secret.get_secret_value(),
                    "api_passphrase": cfg.polymarket.api_passphrase.get_secret_value(),
                },
            },
            "HYPERLIQUID": {
                "factory": HyperliquidLiveDataClientFactory,
                "config": {
                    "http_url": cfg.hyperliquid.http_url,
                    "ws_url": cfg.hyperliquid.ws_url,
                    "account_address": cfg.hyperliquid.account_address,
                },
            },
        },
        exec_clients={
            "POLYMARKET": {
                "factory": PolymarketLiveExecClientFactory,
                "config": {
                    "http_url": cfg.polymarket.http_url,
                    "private_key": cfg.polymarket.private_key.get_secret_value(),
                    "api_key": cfg.polymarket.api_key,
                    "api_secret": cfg.polymarket.api_secret.get_secret_value(),
                    "api_passphrase": cfg.polymarket.api_passphrase.get_secret_value(),
                    "exchange_address": cfg.polymarket.exchange_address,
                    "is_paper": mode == TradingMode.PAPER,
                },
            },
            "HYPERLIQUID": {
                "factory": HyperliquidLiveExecClientFactory,
                "config": {
                    "http_url": cfg.hyperliquid.http_url,
                    "private_key": cfg.hyperliquid.private_key.get_secret_value(),
                    "account_address": cfg.hyperliquid.account_address,
                    "is_paper": mode == TradingMode.PAPER,
                },
            },
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
    )

    node = TradingNode(config=node_config)

    # Register venue client factories
    node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)
    node.add_exec_client_factory("POLYMARKET", PolymarketLiveExecClientFactory)
    node.add_data_client_factory("HYPERLIQUID", HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory("HYPERLIQUID", HyperliquidLiveExecClientFactory)

    # Add strategies
    node.trader.add_strategy(
        MarketMakingStrategy(
            config=MarketMakingConfig(
                spread_bps=cfg.market_maker.spread_bps,
                order_size_usdc=cfg.market_maker.order_size_usdc,
                max_position_usdc=cfg.market_maker.max_position_usdc,
            )
        )
    )
    node.trader.add_strategy(
        BinaryArbStrategy(
            config=BinaryArbConfig(
                min_profit_usdc=cfg.arb.min_profit_usdc,
                max_capital_usdc=cfg.arb.max_capital_usdc,
            )
        )
    )

    return node
