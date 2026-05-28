"""
HyperliquidRunner — TradingNode-driven paper + live runs for HL.

Mirrors `PaperRunnerV2` (Polymarket) but for the Hyperliquid venue:

  - `paper`  : `is_paper=True` on the exec client. `HyperliquidPaperFillEngine`
               is registered as an actor and emits OrderFilled/OrderCanceled
               events from the live book — no network writes to HL.
  - `testnet`: `is_paper=False` against the testnet endpoint
               (`https://api.hyperliquid-testnet.xyz`). Real signing, real
               fills, faucet USDC. NO `LIVE_TRADING_CONFIRMED` requirement.
  - `mainnet`: `is_paper=False` against mainnet. Requires the full
               triple-gate (`LIVE_TRADING_CONFIRMED`, hypothesis state=LIVE,
               and the `--i-understand-this-is-live` CLI flag enforced at
               the script level).

The runner is synchronous and must be called from the main thread (NT
TradingNode installs signal handlers on construction).
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from trading_lab.config import TradingConfig, live_trading_confirmed

log = logging.getLogger(__name__)

Network = Literal["mainnet", "testnet"]


class HyperliquidRunnerError(Exception):
    """Raised when an HL run cannot start (missing keys, missing gates)."""


@dataclass
class HyperliquidRunSummary:
    slug: str
    instruments: int
    network: str
    is_paper: bool
    duration_secs: float


class HyperliquidRunner:
    """
    Synchronous TradingNode runner for Hyperliquid.

    Parameters
    ----------
    config : TradingConfig
    slug : str
        Hypothesis slug.
    strategy_module / strategy_class / strategy_config_class : str
    symbols : list[str]
        Coin tickers (e.g. ["BTC", "ETH"]) — converted to `BTC-PERP.HYPERLIQUID`
        instrument ids by the runner.
    is_paper : bool
        True = paper mode (no real order writes; fills synthesized).
        False = real signing against the configured network.
    network : "mainnet" | "testnet"
        Endpoint selection. Mainnet is real money and requires the
        full triple-gate (see script-level checks). Testnet uses faucet
        USDC and only requires the lifecycle state gate.
    strategy_params : dict
    duration_secs : int | None
    """

    def __init__(
        self,
        config: TradingConfig,
        slug: str,
        strategy_module: str,
        strategy_class: str,
        strategy_config_class: str,
        symbols: list[str],
        is_paper: bool,
        network: Network = "mainnet",
        strategy_params: dict[str, Any] | None = None,
        duration_secs: int | None = None,
    ) -> None:
        if network not in ("mainnet", "testnet"):
            raise HyperliquidRunnerError(
                f"network must be 'mainnet' or 'testnet', got {network!r}"
            )

        # Gating for non-paper (real-network) runs.
        if not is_paper:
            if network == "mainnet" and not live_trading_confirmed():
                raise HyperliquidRunnerError(
                    "Hyperliquid mainnet requires LIVE_TRADING_CONFIRMED=true. "
                    "Testnet runs do not need this env gate."
                )
            secrets = config.hyperliquid_secrets
            if network == "mainnet" and not secrets.has_credentials:
                raise HyperliquidRunnerError(
                    "HL_PRIVATE_KEY missing — required for HL mainnet."
                )
            if network == "testnet" and not secrets.has_testnet_credentials:
                raise HyperliquidRunnerError(
                    "HL_TESTNET_PRIVATE_KEY missing — required for HL testnet."
                )

        self._config = config
        self._slug = slug
        self._strategy_module = strategy_module
        self._strategy_class = strategy_class
        self._strategy_config_class = strategy_config_class
        self._symbols = symbols
        self._is_paper = is_paper
        self._network = network
        self._params = strategy_params or {}
        self._duration_secs = duration_secs

    def run(self) -> HyperliquidRunSummary:
        from nautilus_trader.config import (
            ImportableActorConfig,
            ImportableStrategyConfig,
            LiveExecEngineConfig,
            LoggingConfig,
            TradingNodeConfig,
        )
        from nautilus_trader.live.node import TradingNode
        from nautilus_trader.model.identifiers import TraderId

        from trading_lab.agent.events import emit_event
        from trading_lab.venues.hyperliquid.factory import (
            HyperliquidDataClientConfig,
            HyperliquidExecClientConfig,
            HyperliquidLiveDataClientFactory,
            HyperliquidLiveExecClientFactory,
        )
        from trading_lab.venues.hyperliquid.instruments import make_hl_perpetual

        hl_network = self._config.venues.hyperliquid.active(self._network)
        secrets = self._config.hyperliquid_secrets
        pk = secrets.network_private_key(self._network)
        acct = secrets.network_account_address(self._network)

        instruments = [make_hl_perpetual(sym) for sym in self._symbols]
        trader_prefix = "PAPER" if self._is_paper else (
            "TESTNET" if self._network == "testnet" else "LIVE"
        )

        cfg_filtered = self._filter_strategy_params()
        encodable_params: dict[str, Any] = {
            k: v for k, v in cfg_filtered.items()
            if isinstance(v, int | float | str | bool)
        }
        strategy_cfg = ImportableStrategyConfig(
            strategy_path=f"{self._strategy_module}:{self._strategy_class}",
            config_path=f"{self._strategy_module}:{self._strategy_config_class}",
            config=encodable_params,
        )

        actors: list[ImportableActorConfig] = []
        if self._is_paper:
            taker_bps = self._config.portfolio.hyperliquid_fees.taker_bps
            actors.append(
                ImportableActorConfig(
                    actor_path="trading_lab.venues.hyperliquid.paper_fill:HyperliquidPaperFillEngine",
                    config_path="trading_lab.venues.hyperliquid.paper_fill:HyperliquidPaperFillConfig",
                    config={
                        "component_id": "HYPERLIQUID-PAPER-FILL",
                        "ioc_max_book_updates": 1,
                        "account_currency": "USDC",
                        "taker_bps": float(taker_bps),
                    },
                )
            )

        node_config = TradingNodeConfig(
            trader_id=TraderId(f"{trader_prefix}-{self._slug[:16].upper()}"),
            logging=LoggingConfig(log_level=self._config.log_level),
            exec_engine=LiveExecEngineConfig(reconciliation=not self._is_paper),
            data_clients={
                "HYPERLIQUID": HyperliquidDataClientConfig(
                    http_url=hl_network.api_url,
                    ws_url=hl_network.ws_url,
                    private_key=pk,
                    account_address=acct,
                ),
            },
            exec_clients={
                "HYPERLIQUID": HyperliquidExecClientConfig(
                    http_url=hl_network.api_url,
                    ws_url=hl_network.ws_url,
                    private_key=pk,
                    account_address=acct,
                    is_paper=self._is_paper,
                ),
            },
            actors=actors,
            strategies=[strategy_cfg],
            timeout_connection=30.0,
        )

        node = TradingNode(config=node_config)
        node.add_data_client_factory("HYPERLIQUID", HyperliquidLiveDataClientFactory)
        node.add_exec_client_factory("HYPERLIQUID", HyperliquidLiveExecClientFactory)
        node.build()

        # Add instruments to the cache so strategies and the fill engine
        # can resolve them.
        for instr in instruments:
            try:
                node.cache.add_instrument(instr)
            except Exception as exc:
                log.warning("could not add HL instrument %s: %s", instr.id, exc)

        # Wire the fill engine ↔ exec client and pre-register instruments.
        if self._is_paper:
            fill_engine = self._find_actor(node, "HYPERLIQUID-PAPER-FILL")
            exec_client = self._find_exec_client(node, "HYPERLIQUID")
            if exec_client is not None and fill_engine is not None:
                exec_client._paper_fill_engine = fill_engine  # type: ignore[attr-defined]
                for instr in instruments:
                    fill_engine.register_instrument(instr.id)

        strategy = self._find_strategy(node, self._strategy_class)
        if strategy is not None:
            for instr in instruments:
                if hasattr(strategy, "register_instrument"):
                    strategy.register_instrument(instr.id)

        emit_event(
            type="runner_start",
            summary=(
                f"{self._slug}: HL runner starting "
                f"(network={self._network}, is_paper={self._is_paper}, "
                f"symbols={len(instruments)})"
            ),
            severity="warn" if not self._is_paper else "info",
            slug=self._slug,
            data={
                "venue": "hyperliquid",
                "network": self._network,
                "is_paper": self._is_paper,
                "hypothesis_slug": self._slug,
                "symbols": list(self._symbols),
                "duration_secs": self._duration_secs,
            },
        )

        start_ts = time.monotonic()
        if self._duration_secs:
            def _timer():
                time.sleep(self._duration_secs)
                log.info("HyperliquidRunner timer expired; stopping node")
                try:
                    node.stop()
                except Exception as exc:
                    log.warning("timer node.stop error: %s", exc)

            threading.Thread(target=_timer, daemon=True, name="hl-runner-timer").start()

        try:
            node.run()
        except KeyboardInterrupt:
            log.info("HyperliquidRunner interrupted")
            try:
                node.stop()
            except Exception:
                pass
        except Exception as exc:
            log.warning("node.run raised: %s", exc)

        return HyperliquidRunSummary(
            slug=self._slug,
            instruments=len(instruments),
            network=self._network,
            is_paper=self._is_paper,
            duration_secs=round(time.monotonic() - start_ts, 2),
        )

    # ------------------------------------------------------------------

    def _filter_strategy_params(self) -> dict[str, Any]:
        try:
            mod = importlib.import_module(self._strategy_module)
            cfg_cls = getattr(mod, self._strategy_config_class)
            allowed = set(getattr(cfg_cls, "__struct_fields__", ()))
            if allowed:
                return {k: v for k, v in self._params.items() if k in allowed}
        except Exception as exc:
            log.warning("could not introspect strategy config: %s", exc)
        return self._params

    def _find_actor(self, node, component_id: str):
        try:
            for actor in node.trader.actors():
                if str(actor.id) == component_id:
                    return actor
        except Exception:
            pass
        return None

    def _find_strategy(self, node, class_name: str):
        try:
            for strat in node.trader.strategies():
                if type(strat).__name__ == class_name:
                    return strat
        except Exception:
            pass
        return None

    def _find_exec_client(self, node, venue_str: str):
        try:
            from nautilus_trader.model.identifiers import ClientId

            return node.kernel.exec_engine._clients[ClientId(venue_str)]  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("_find_exec_client failed: %s", exc)
            return None
