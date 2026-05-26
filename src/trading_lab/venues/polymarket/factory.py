"""
Polymarket client factories for NautilusTrader's dependency injection.

Two pieces per client:
  1. A `LiveDataClientConfig` / `LiveExecClientConfig` subclass declaring
     the custom fields (api_key, ws_url, etc). The TradingNode validates
     against these.
  2. A factory class that gets the parsed config + node services and
     returns a wired client instance.

The factory is registered with the node via
`node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)`
AFTER `TradingNodeConfig(data_clients={"POLYMARKET": PolymarketDataClientConfig(...)})`.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.live.config import LiveDataClientConfig, LiveExecClientConfig
from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory

from trading_lab.venues.polymarket.auth import L2Credentials
from trading_lab.venues.polymarket.client import PolymarketRestClient, PolymarketWsClient
from trading_lab.venues.polymarket.data import PolymarketDataClient
from trading_lab.venues.polymarket.execution import PolymarketExecutionClient

_PM_WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_PM_WS_USER = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


class PolymarketDataClientConfig(LiveDataClientConfig, frozen=True):
    """Config for Polymarket data client (market WS + REST)."""

    http_url: str = "https://clob.polymarket.com"
    ws_url: str = _PM_WS_MARKET
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""


class PolymarketExecClientConfig(LiveExecClientConfig, frozen=True):
    """Config for Polymarket execution client (REST + user-channel WS)."""

    http_url: str = "https://clob.polymarket.com"
    ws_url: str = _PM_WS_USER
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    private_key: str = ""
    exchange_address: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    is_paper: bool = True


def _cfg_get(config, name: str, default=None):
    """Read a field from either a dict OR an msgspec.Struct config."""
    if hasattr(config, name):
        return getattr(config, name)
    if isinstance(config, dict):
        return config.get(name, default)
    return default


class PolymarketLiveDataClientFactory(LiveDataClientFactory):
    """Factory for PolymarketDataClient."""

    @staticmethod
    def create(
        loop,
        name: str,
        config: Any,
        msgbus,
        cache,
        clock,
    ) -> PolymarketDataClient:
        creds = L2Credentials(
            api_key=_cfg_get(config, "api_key", ""),
            api_secret=_cfg_get(config, "api_secret", ""),
            api_passphrase=_cfg_get(config, "api_passphrase", ""),
        )
        rest = PolymarketRestClient(
            http_url=_cfg_get(config, "http_url"), creds=creds,
        )

        ws = PolymarketWsClient(
            ws_url=_cfg_get(config, "ws_url"),
            creds=creds,
            on_message=lambda msg: None,  # wired after construction below
        )

        client = PolymarketDataClient(
            loop=loop,
            client=rest,
            ws_client=ws,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )

        ws._on_message = client._handle_ws_message
        return client


class PolymarketLiveExecClientFactory(LiveExecClientFactory):
    """Factory for PolymarketExecutionClient."""

    @staticmethod
    def create(
        loop,
        name: str,
        config: Any,
        msgbus,
        cache,
        clock,
    ) -> PolymarketExecutionClient:
        creds = L2Credentials(
            api_key=_cfg_get(config, "api_key", ""),
            api_secret=_cfg_get(config, "api_secret", ""),
            api_passphrase=_cfg_get(config, "api_passphrase", ""),
        )
        rest = PolymarketRestClient(
            http_url=_cfg_get(config, "http_url"), creds=creds,
        )

        # The ws client for exec (user channel) is shared with the data client
        # in production; here we create a standalone one for exec events.
        ws = PolymarketWsClient(
            ws_url=_cfg_get(config, "ws_url", ""),
            creds=creds,
            on_message=lambda msg: None,
        )

        client = PolymarketExecutionClient(
            loop=loop,
            rest=rest,
            ws=ws,
            private_key=_cfg_get(config, "private_key", ""),
            exchange_address=_cfg_get(config, "exchange_address", ""),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            is_paper=bool(_cfg_get(config, "is_paper", True)),
        )

        ws._on_message = client.handle_user_ws_message
        return client
