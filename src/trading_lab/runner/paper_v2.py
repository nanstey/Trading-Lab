"""
PaperRunnerV2 — TradingNode-driven paper trading.

Replaces the GenericPaperRunner. Strategies run through the REAL
`PolymarketExecutionClient` with `is_paper=True`, which now delegates
fills to `PolymarketPaperFillEngine` (an Actor wired alongside the
strategy on the message bus).

Why this is "realistic":
  - Strategy uses `self.order_factory.limit(...)` — real NT order objects.
  - Strategy calls `self.submit_order(order)` — real NT submission path.
  - The execution engine routes the command through
    `PolymarketExecutionClient._submit_order` — real venue-client code.
  - In `is_paper=True`, the order is *not* sent to PM (no money risk) but
    is registered with the paper-fill engine.
  - The fill engine watches the live book and emits real `OrderFilled` /
    `OrderCanceled` events via the message bus.
  - The strategy receives those events through the same path live would
    use. Position / PnL accounting goes through NT's portfolio engine.

The only differences vs live mode:
  - No real venue submission (no money moves).
  - VenueOrderId is "PAPER-..." instead of a Polymarket order id.
  - Commission is set to 0.

Flipping `is_paper=False` in the exec factory makes this LIVE.

Threading model
---------------
TradingNode sets up signal handlers on construction, which only works
on the main thread. So:
  - The runner's `run()` method is SYNCHRONOUS and must be called from
    the main thread.
  - Duration-bounded runs use a timer thread that calls `node.stop()`
    after `duration_secs`.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from trading_lab.config import TradingConfig

log = logging.getLogger(__name__)


@dataclass
class PaperRunV2Summary:
    slug: str
    instruments: int
    duration_secs: float
    kill_switch_triggered: bool


class PaperRunnerV2:
    """
    Synchronous TradingNode-driven paper runner. Call from main thread.

    Parameters
    ----------
    config : TradingConfig
        Must have trading_mode == PAPER.
    slug : str
        Hypothesis slug.
    strategy_module / strategy_class / strategy_config_class : str
        Where to import the strategy from.
    pairs : list[(condition_id, yes_token_id, no_token_id)]
    strategy_params : dict
        Kwargs to pass to the *Config constructor.
    duration_secs : int | None
        Auto-stop after N seconds via a timer thread. None = until SIGINT.
    """

    def __init__(
        self,
        config: TradingConfig,
        slug: str,
        strategy_module: str,
        strategy_class: str,
        strategy_config_class: str,
        pairs: list[tuple[str, str, str]],
        strategy_params: dict[str, Any] | None = None,
        duration_secs: int | None = None,
    ) -> None:
        # Paper-vs-live is per-strategy (hypothesis.state), not system-wide.
        # The runner key is `is_paper=True` on the exec config below — that's
        # what makes this "paper". Caller is responsible for matching with
        # hypothesis state (paper_run_v2.py enforces that).
        self._config = config
        self._slug = slug
        self._strategy_module = strategy_module
        self._strategy_class = strategy_class
        self._strategy_config_class = strategy_config_class
        self._pairs = pairs
        self._params = strategy_params or {}
        self._duration_secs = duration_secs

    def run(self) -> PaperRunV2Summary:
        from nautilus_trader.config import (
            ImportableActorConfig,
            ImportableStrategyConfig,
            LiveExecEngineConfig,
            LoggingConfig,
            TradingNodeConfig,
        )
        from nautilus_trader.live.node import TradingNode
        from nautilus_trader.model.identifiers import TraderId

        from trading_lab.data.parquet_loader import make_instrument
        from trading_lab.risk.kill_switch import KillSwitch
        from trading_lab.venues.hyperliquid.factory import (
            HyperliquidLiveDataClientFactory,
            HyperliquidLiveExecClientFactory,
        )
        from trading_lab.venues.polymarket.factory import (
            PolymarketDataClientConfig,
            PolymarketExecClientConfig,
            PolymarketLiveDataClientFactory,
            PolymarketLiveExecClientFactory,
        )

        instruments = {}
        for cid, yes_id, no_id in self._pairs:
            instruments[yes_id] = make_instrument(yes_id, cid)
            instruments[no_id] = make_instrument(no_id, cid)

        async def _noop_cancel_all() -> None:
            pass

        kill_switch = KillSwitch(
            daily_loss_limit_usdc=self._config.risk.daily_loss_limit_usdc,
            cancel_all_fn=_noop_cancel_all,
        )

        cfg_filtered = self._filter_strategy_params()
        encodable_params: dict[str, Any] = {
            k: v for k, v in cfg_filtered.items()
            if isinstance(v, int | float | str | bool)
        }

        fill_actor_cfg = ImportableActorConfig(
            actor_path="trading_lab.venues.polymarket.paper_fill:PolymarketPaperFillEngine",
            config_path="trading_lab.venues.polymarket.paper_fill:PolymarketPaperFillConfig",
            config={
                "component_id": "POLYMARKET-PAPER-FILL",
                "ioc_max_book_updates": 1,
                "account_currency": "USDC",
            },
        )

        strategy_cfg = ImportableStrategyConfig(
            strategy_path=f"{self._strategy_module}:{self._strategy_class}",
            config_path=f"{self._strategy_module}:{self._strategy_config_class}",
            config=encodable_params,
        )

        node_config = TradingNodeConfig(
            trader_id=TraderId(f"PAPER-{self._slug[:16].upper()}"),
            logging=LoggingConfig(log_level=self._config.log_level),
            exec_engine=LiveExecEngineConfig(reconciliation=False),
            data_clients={
                "POLYMARKET": PolymarketDataClientConfig(
                    http_url=self._config.polymarket.host,
                    api_key=self._config.polymarket.api_key,
                    api_secret=self._config.polymarket.api_secret.get_secret_value(),
                    api_passphrase=self._config.polymarket.api_passphrase.get_secret_value(),
                ),
            },
            exec_clients={
                "POLYMARKET": PolymarketExecClientConfig(
                    http_url=self._config.polymarket.host,
                    private_key=self._config.polymarket.private_key.get_secret_value(),
                    api_key=self._config.polymarket.api_key,
                    api_secret=self._config.polymarket.api_secret.get_secret_value(),
                    api_passphrase=self._config.polymarket.api_passphrase.get_secret_value(),
                    exchange_address=self._config.polymarket.exchange_address,
                    is_paper=True,
                ),
            },
            actors=[fill_actor_cfg],
            strategies=[strategy_cfg],
            timeout_connection=30.0,
        )

        node = TradingNode(config=node_config)
        node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)
        node.add_exec_client_factory("POLYMARKET", PolymarketLiveExecClientFactory)
        try:
            node.add_data_client_factory("HYPERLIQUID", HyperliquidLiveDataClientFactory)
            node.add_exec_client_factory("HYPERLIQUID", HyperliquidLiveExecClientFactory)
        except Exception:
            pass
        node.build()

        # Wire fill engine + data client + register strategy pairs + add
        # instruments to cache.
        fill_engine = self._find_actor(node, "POLYMARKET-PAPER-FILL")
        exec_client = self._find_exec_client(node, "POLYMARKET")
        data_client = self._find_data_client(node, "POLYMARKET")

        # Tell the data client the short-symbol → full-token mapping so
        # `subscribe_trade_ticks` can issue a real WS market subscribe.
        token_map: dict[str, str] = {}
        for cid, yes_id, no_id in self._pairs:
            token_map[instruments[yes_id].symbol.value] = yes_id
            token_map[instruments[no_id].symbol.value] = no_id
        if data_client is not None:
            data_client.register_tokens(token_map)
            log.info("data_client found; registered %d tokens", len(token_map))
        else:
            log.warning("data_client not found via _data_engine.get_client; "
                        "subscriptions will fail")

        if exec_client is not None and fill_engine is not None:
            exec_client._paper_fill_engine = fill_engine  # type: ignore[attr-defined]
            for instr in instruments.values():
                fill_engine.register_instrument(instr.id)

        # Wire per-strategy capital allocator (Portfolio-backed pre-trade gate).
        if exec_client is not None:
            from trading_lab.agent import portfolio as _alloc_mod
            from trading_lab.agent.events import emit_event

            warnings = _alloc_mod.validate_allocations(self._config)
            for w in warnings:
                log.warning("allocator config warning: %s", w)
                emit_event(
                    type="portfolio_config_warning",
                    summary=w, severity="warn", slug=self._slug, data={},
                )

            equity_provider = self._build_equity_provider()
            allocator = _alloc_mod.for_slug(
                self._slug, self._config, equity_provider=equity_provider,
            )
            try:
                allocator.set_portfolio(node.portfolio)
            except Exception as exc:
                log.warning("could not attach portfolio to allocator: %s", exc)
            exec_client._portfolio_allocator = allocator  # type: ignore[attr-defined]
            emit_event(
                type="portfolio_alloc_armed",
                summary=(
                    f"{self._slug}: paper allocator armed cap=$"
                    f"{allocator.cap_usdc:.2f} ({allocator.cap_spec.describe()})"
                ),
                severity="info", slug=self._slug,
                data=allocator.snapshot(),
            )

        strategy = self._find_strategy(node, self._strategy_class)
        if strategy is not None:
            for cid, yes_id, no_id in self._pairs:
                yes_iid = instruments[yes_id].id
                no_iid = instruments[no_id].id
                if hasattr(strategy, "register_market_pair"):
                    strategy.register_market_pair(cid, yes_iid, no_iid)
                elif hasattr(strategy, "register_instrument"):
                    strategy.register_instrument(yes_iid)
                    strategy.register_instrument(no_iid)
                else:
                    strategy._initial_pair = (cid, yes_iid, no_iid)  # type: ignore[attr-defined]

        for instr in instruments.values():
            try:
                node.cache.add_instrument(instr)
            except Exception:
                pass

        log.info(
            "PaperRunnerV2 starting | slug=%s strategy=%s pairs=%d instruments=%d",
            self._slug, self._strategy_class, len(self._pairs), len(instruments),
        )

        # Timer thread: after duration_secs, call node.stop(). node.stop()
        # schedules stop_async on the kernel's loop → node.run() returns
        # cleanly.
        start_ts = time.monotonic()
        if self._duration_secs:
            def _timer():
                time.sleep(self._duration_secs)
                log.info("PaperRunnerV2 timer expired; stopping node")
                try:
                    node.stop()
                except Exception as exc:
                    log.warning("timer node.stop error: %s", exc)

            t = threading.Thread(target=_timer, daemon=True, name="paper-runner-timer")
            t.start()

        # Blocking — runs the kernel loop until node.stop() is called.
        try:
            node.run()
        except KeyboardInterrupt:
            log.info("PaperRunnerV2 interrupted")
            try:
                node.stop()
            except Exception:
                pass
        except Exception as exc:
            log.warning("node.run raised: %s", exc)

        duration = time.monotonic() - start_ts
        return PaperRunV2Summary(
            slug=self._slug,
            instruments=len(instruments),
            duration_secs=round(duration, 2),
            kill_switch_triggered=kill_switch.is_triggered,
        )

    # ------------------------------------------------------------------
    # Helpers
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

            cid = ClientId(venue_str)
            return node.kernel.exec_engine._clients[cid]  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("_find_exec_client failed: %s", exc)
            return None

    def _find_data_client(self, node, venue_str: str):
        try:
            from nautilus_trader.model.identifiers import ClientId

            cid = ClientId(venue_str)
            return node.kernel.data_engine._clients[cid]  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("_find_data_client failed: %s", exc)
            return None

    def _build_equity_provider(self):
        """
        Build + prime a `PolymarketEquityProvider`. Returns None if the
        wallet isn't configured (allocator falls back to absolute caps).

        Priming is best-effort: a failed refresh during paper startup
        is logged but doesn't block the run. Subsequent `check_order`
        calls against a pct cap will return 0 (block) until refreshed.
        """
        import asyncio

        from trading_lab.agent.venue_equity import PolymarketEquityProvider
        from trading_lab.venues.polymarket.auth import L2Credentials, derive_address
        from trading_lab.venues.polymarket.client import PolymarketRestClient

        pk = self._config.polymarket.private_key.get_secret_value()
        if not pk:
            log.warning("equity: no POLY_PRIVATE_KEY; pct caps will resolve to 0")
            return None
        try:
            wallet = derive_address(pk)
        except Exception as exc:
            log.warning("equity: could not derive wallet address: %s", exc)
            return None

        rest = None
        if self._config.polymarket.has_l2_credentials:
            try:
                creds = L2Credentials(
                    api_key=self._config.polymarket.api_key,
                    api_secret=self._config.polymarket.api_secret.get_secret_value(),
                    api_passphrase=self._config.polymarket.api_passphrase.get_secret_value(),
                )
                rest = PolymarketRestClient(
                    http_url=self._config.polymarket.host, creds=creds,
                )
            except Exception as exc:
                log.debug("equity: could not build PM rest client: %s", exc)

        provider = PolymarketEquityProvider(wallet_address=wallet, rest_client=rest)
        try:
            asyncio.run(provider.refresh())
            log.info(
                "equity: primed for %s — total=$%.2f (source=%s)",
                wallet[:10] + "...",
                provider.current_usdc(),
                provider.snapshot.source if provider.snapshot else "n/a",
            )
        except Exception as exc:
            log.warning("equity: initial refresh failed (will retry on demand): %s", exc)
        return provider
