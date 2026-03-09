"""
Polymarket client factories for NautilusTrader's dependency injection.

NautilusTrader discovers venue adapters via factory classes that accept a
standardised config dict and return fully wired DataClient/ExecutionClient.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory

from nautilus_predict.venues.polymarket.auth import L2Credentials
from nautilus_predict.venues.polymarket.client import PolymarketRestClient, PolymarketWsClient
from nautilus_predict.venues.polymarket.data import PolymarketDataClient
from nautilus_predict.venues.polymarket.execution import PolymarketExecutionClient


class PolymarketLiveDataClientFactory(LiveDataClientFactory):
    """Factory for PolymarketDataClient."""

    @staticmethod
    def create(
        loop,
        name: str,
        config: dict[str, Any],
        msgbus,
        cache,
        clock,
    ) -> PolymarketDataClient:
        creds = L2Credentials(
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            api_passphrase=config["api_passphrase"],
        )
        rest = PolymarketRestClient(http_url=config["http_url"], creds=creds)

        ws = PolymarketWsClient(
            ws_url=config["ws_url"],
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

        # Wire ws message handler to the client instance
        ws._on_message = client._handle_ws_message
        return client


class PolymarketLiveExecClientFactory(LiveExecClientFactory):
    """Factory for PolymarketExecutionClient."""

    @staticmethod
    def create(
        loop,
        name: str,
        config: dict[str, Any],
        msgbus,
        cache,
        clock,
    ) -> PolymarketExecutionClient:
        creds = L2Credentials(
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            api_passphrase=config["api_passphrase"],
        )
        rest = PolymarketRestClient(http_url=config["http_url"], creds=creds)

        # The ws client for exec (user channel) is shared with the data client
        # in production; here we create a standalone one for exec events.
        ws = PolymarketWsClient(
            ws_url=config.get("ws_url", ""),
            creds=creds,
            on_message=lambda msg: None,
        )

        client = PolymarketExecutionClient(
            loop=loop,
            rest=rest,
            ws=ws,
            private_key=config["private_key"],
            exchange_address=config["exchange_address"],
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            is_paper=config.get("is_paper", True),
        )

        ws._on_message = client.handle_user_ws_message
        return client
