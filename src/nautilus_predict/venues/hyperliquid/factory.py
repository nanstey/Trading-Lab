"""Hyperliquid client factories for NautilusTrader's dependency injection."""

from __future__ import annotations

from typing import Any

from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory

from nautilus_predict.venues.hyperliquid.client import HyperliquidRestClient, HyperliquidWsClient
from nautilus_predict.venues.hyperliquid.data import HyperliquidDataClient
from nautilus_predict.venues.hyperliquid.execution import HyperliquidExecutionClient


class HyperliquidLiveDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(
        loop,
        name: str,
        config: dict[str, Any],
        msgbus,
        cache,
        clock,
    ) -> HyperliquidDataClient:
        rest = HyperliquidRestClient(
            http_url=config["http_url"],
            private_key=config.get("private_key", ""),
            account_address=config.get("account_address", ""),
        )
        ws = HyperliquidWsClient(
            ws_url=config["ws_url"],
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
        config: dict[str, Any],
        msgbus,
        cache,
        clock,
    ) -> HyperliquidExecutionClient:
        rest = HyperliquidRestClient(
            http_url=config["http_url"],
            private_key=config["private_key"],
            account_address=config.get("account_address", ""),
        )
        ws = HyperliquidWsClient(
            ws_url=config.get("ws_url", ""),
            on_message=lambda msg: None,
        )
        client = HyperliquidExecutionClient(
            loop=loop,
            rest=rest,
            ws=ws,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            is_paper=config.get("is_paper", True),
        )
        ws._on_message = client.handle_ws_message
        return client
