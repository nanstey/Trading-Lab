"""
Legacy NautilusTrader TradingNode factory.

DEPRECATED — superseded by `runner/paper_v2.py` (PaperRunnerV2) and
`runner/live_v2.py` (LiveRunner) which both build their own TradingNode
inline. This module is kept only because `main.py` historically called
into it; new code should import `PaperRunnerV2` or `LiveRunner` directly.

`is_paper` is now derived from the *hypothesis state*, not a system-wide
TRADING_MODE env var. Callers that still need this entry point pass an
explicit `is_paper` bool.
"""

from __future__ import annotations

from nautilus_trader.config import (
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from nautilus_predict.config import load_config
from nautilus_predict.strategies.arb_complement import BinaryArbConfig, BinaryArbStrategy
from nautilus_predict.venues.hyperliquid.factory import (
    HyperliquidLiveDataClientFactory,
    HyperliquidLiveExecClientFactory,
)
from nautilus_predict.venues.polymarket.factory import (
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
    PolymarketLiveDataClientFactory,
    PolymarketLiveExecClientFactory,
)


def build_node(is_paper: bool = True) -> TradingNode:
    """Construct a fully configured TradingNode. Legacy entry — prefer PaperRunnerV2."""
    cfg = load_config()

    node_config = TradingNodeConfig(
        trader_id=TraderId("Trading Lab-001"),
        logging=LoggingConfig(log_level=cfg.log_level, log_colors=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=not is_paper,
            reconciliation_lookback_mins=15,
        ),
        data_clients={
            "POLYMARKET": PolymarketDataClientConfig(
                http_url=cfg.venues.polymarket.http_url,
                ws_url=cfg.venues.polymarket.ws_market_url,
                api_key=cfg.polymarket_secrets.api_key,
                api_secret=cfg.polymarket_secrets.api_secret.get_secret_value(),
                api_passphrase=cfg.polymarket_secrets.api_passphrase.get_secret_value(),
            ),
        },
        exec_clients={
            "POLYMARKET": PolymarketExecClientConfig(
                http_url=cfg.venues.polymarket.http_url,
                ws_url=cfg.venues.polymarket.ws_user_url,
                private_key=cfg.polymarket_secrets.private_key.get_secret_value(),
                api_key=cfg.polymarket_secrets.api_key,
                api_secret=cfg.polymarket_secrets.api_secret.get_secret_value(),
                api_passphrase=cfg.polymarket_secrets.api_passphrase.get_secret_value(),
                exchange_address=cfg.venues.polymarket.exchange_address,
                is_paper=is_paper,
            ),
        },
        timeout_connection=30.0,
    )

    node = TradingNode(config=node_config)
    node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)
    node.add_exec_client_factory("POLYMARKET", PolymarketLiveExecClientFactory)
    node.add_data_client_factory("HYPERLIQUID", HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory("HYPERLIQUID", HyperliquidLiveExecClientFactory)

    # Add a default arb strategy as the original behaviour. Modern callers
    # build their TradingNodeConfig with `strategies=[...]` instead and
    # don't use this function.
    node.trader.add_strategy(
        BinaryArbStrategy(config=BinaryArbConfig())
    )
    return node
