"""Hyperliquid client factories for NautilusTrader's dependency injection.

Two pieces per client:
  1. A `LiveDataClientConfig` / `LiveExecClientConfig` subclass declaring
     the custom fields (http_url, ws_url, private_key, is_paper, ...).
  2. A factory class that gets the parsed config + node services and
     returns a wired client instance.

The factory is registered on the node via
`node.add_data_client_factory("HYPERLIQUID", HyperliquidLiveDataClientFactory)`.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.live.config import LiveDataClientConfig, LiveExecClientConfig
from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory

from trading_lab.venues.hyperliquid.client import HyperliquidRestClient, HyperliquidWsClient
from trading_lab.venues.hyperliquid.data import HyperliquidDataClient
from trading_lab.venues.hyperliquid.endpoints import MAINNET_HTTP_URL, MAINNET_WS_URL
from trading_lab.venues.hyperliquid.execution import HyperliquidExecutionClient

# Dummy key — the HL REST client requires a private_key at construction
# to derive an address, but in paper mode it is never used for signing.
# Real keys are required for any non-paper run.
_DUMMY_PAPER_KEY = "0x" + "11" * 32


class HyperliquidDataClientConfig(LiveDataClientConfig, frozen=True):
    """Config for Hyperliquid data client (market WS + REST info)."""

    http_url: str = MAINNET_HTTP_URL
    ws_url: str = MAINNET_WS_URL
    private_key: str = ""
    account_address: str = ""


class HyperliquidExecClientConfig(LiveExecClientConfig, frozen=True):
    """Config for Hyperliquid execution client (REST exchange + user WS)."""

    http_url: str = MAINNET_HTTP_URL
    ws_url: str = MAINNET_WS_URL
    private_key: str = ""
    account_address: str = ""
    is_paper: bool = True


def _cfg_get(config: Any, name: str, default=None):
    """Read a field from either a dict OR an msgspec.Struct config."""
    if hasattr(config, name):
        return getattr(config, name)
    if isinstance(config, dict):
        return config.get(name, default)
    return default


class HyperliquidLiveDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(
        loop,
        name: str,
        config: Any,
        msgbus,
        cache,
        clock,
    ) -> HyperliquidDataClient:
        # Data client is read-only; in paper mode no private key is
        # required, but the REST helper still derives an address from a
        # key. Fall back to a dummy when unset.
        pk = _cfg_get(config, "private_key", "") or _DUMMY_PAPER_KEY
        rest = HyperliquidRestClient(
            http_url=_cfg_get(config, "http_url", MAINNET_HTTP_URL),
            private_key=pk,
            account_address=_cfg_get(config, "account_address", ""),
        )
        ws = HyperliquidWsClient(
            ws_url=_cfg_get(config, "ws_url", MAINNET_WS_URL),
            on_message=lambda msg: None,
        )
        client = HyperliquidDataClient(
            loop=loop,
            rest=rest,
            ws=ws,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        ws._on_message = client._handle_ws_message
        return client


class HyperliquidLiveExecClientFactory(LiveExecClientFactory):
    @staticmethod
    def create(
        loop,
        name: str,
        config: Any,
        msgbus,
        cache,
        clock,
    ) -> HyperliquidExecutionClient:
        is_paper = bool(_cfg_get(config, "is_paper", True))
        pk = _cfg_get(config, "private_key", "")
        if not pk:
            if is_paper:
                pk = _DUMMY_PAPER_KEY
            else:
                raise ValueError(
                    "HyperliquidExecClientConfig.private_key is required for "
                    "non-paper runs. Set HL_PRIVATE_KEY (mainnet) or "
                    "HL_TESTNET_PRIVATE_KEY (testnet) in .env."
                )
        rest = HyperliquidRestClient(
            http_url=_cfg_get(config, "http_url", MAINNET_HTTP_URL),
            private_key=pk,
            account_address=_cfg_get(config, "account_address", ""),
        )
        ws = HyperliquidWsClient(
            ws_url=_cfg_get(config, "ws_url", MAINNET_WS_URL),
            on_message=lambda msg: None,
        )
        client = HyperliquidExecutionClient(
            loop=loop,
            rest=rest,
            ws=ws,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            is_paper=is_paper,
        )
        ws._on_message = client.handle_ws_message
        return client
