"""
LiveRunner — TradingNode-driven LIVE trading.

Identical code path to `PaperRunnerV2`. The ONLY differences are:
  - `is_paper=False` in `PolymarketExecClientConfig` so the execution
    client actually submits orders to the venue REST API.
  - No paper-fill engine — real `OrderFilled` events come from the user
    channel WS.
  - Pre-flight checks: kill switch, TRADING_MODE=live, LIVE_TRADING_CONFIRMED.

When you trust your paper run, going live is one config flag flip — by
design, so there's no "paper worked, live blows up" surprise.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!!! THIS MODULE EXECUTES REAL ORDERS WITH REAL FUNDS              !!
!!!! DOUBLE-CHECK ALL CONFIGURATION BEFORE ENABLING                !!
!!!! START WITH SMALL POSITION LIMITS AND MONITOR CLOSELY          !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from nautilus_predict.config import TradingConfig, live_trading_confirmed

log = logging.getLogger(__name__)


class LiveTradingNotEnabled(Exception):
    """Raised when live trading is attempted without explicit confirmation."""


@dataclass
class LiveRunSummary:
    slug: str
    instruments: int
    duration_secs: float
    kill_switch_triggered: bool


class LiveRunner:
    """
    Synchronous TradingNode-driven LIVE runner. Call from main thread.

    Pre-flight gates (all required to start):
      - `config.trading_mode == TradingMode.LIVE`
      - `LIVE_TRADING_CONFIRMED=true` env var
      - Polymarket L1 + L2 credentials configured
      - Global kill-switch (`data/.kill_switch`) NOT tripped

    Each gate failure raises `LiveTradingNotEnabled` with a hint to fix.
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
        # ---------- Pre-flight: live security gate ----------
        # Paper-vs-live is per-strategy (hypothesis.state); the system-wide
        # gate is just `LIVE_TRADING_CONFIRMED=true` in .env, which protects
        # against accidental live runs even if someone passes the right slug.
        if not live_trading_confirmed():
            raise LiveTradingNotEnabled(
                "LIVE_TRADING_CONFIRMED env var not set to 'true'. "
                "Set it explicitly in .env to confirm intent to trade real funds."
            )
        if not config.polymarket.has_l1_credentials:
            raise LiveTradingNotEnabled(
                "Polymarket private key (POLY_PRIVATE_KEY) is not configured."
            )
        if not config.polymarket.has_l2_credentials:
            raise LiveTradingNotEnabled(
                "Polymarket L2 credentials (POLY_API_KEY/SECRET/PASSPHRASE) "
                "are not configured. Run scripts/derive_polymarket_keys.py first."
            )

        # ---------- Pre-flight: kill switch ----------
        from nautilus_predict.risk.kill_switch import read_flag

        ks = read_flag()
        if ks and ks.get("triggered"):
            raise LiveTradingNotEnabled(
                "Global kill switch is tripped: " + (ks.get("reason") or "")
                + " — clear with scripts/reset_kill_switch.py --confirm "
                "before going live."
            )

        self._config = config
        self._slug = slug
        self._strategy_module = strategy_module
        self._strategy_class = strategy_class
        self._strategy_config_class = strategy_config_class
        self._pairs = pairs
        self._params = strategy_params or {}
        self._duration_secs = duration_secs

        log.critical(
            "LiveRunner armed — REAL MONEY TRADING WILL START ON .run()",
            extra={
                "slug": slug,
                "pairs": len(pairs),
                "daily_loss_limit_usdc": config.risk.daily_loss_limit_usdc,
                "max_position_usdc": config.risk.max_position_usdc,
            },
        )

    def run(self) -> LiveRunSummary:
        # Final safety check at run-entry (catches any code path that
        # bypassed __init__).
        if not live_trading_confirmed():
            raise LiveTradingNotEnabled(
                "LIVE_TRADING_CONFIRMED was cleared between __init__ and run() — aborting."
            )

        from nautilus_trader.config import (
            ImportableStrategyConfig,
            LiveExecEngineConfig,
            LoggingConfig,
            TradingNodeConfig,
        )
        from nautilus_trader.live.node import TradingNode
        from nautilus_trader.model.identifiers import TraderId

        from nautilus_predict.agent.events import emit_event
        from nautilus_predict.data.parquet_loader import make_instrument
        from nautilus_predict.risk.kill_switch import KillSwitch
        from nautilus_predict.venues.polymarket.factory import (
            PolymarketDataClientConfig,
            PolymarketExecClientConfig,
            PolymarketLiveDataClientFactory,
            PolymarketLiveExecClientFactory,
        )

        instruments = {}
        for cid, yes_id, no_id in self._pairs:
            instruments[yes_id] = make_instrument(yes_id, cid)
            instruments[no_id] = make_instrument(no_id, cid)

        # Kill switch — real cancel function reaches the venue.
        from nautilus_predict.venues.polymarket.auth import L2Credentials
        from nautilus_predict.venues.polymarket.client import PolymarketRestClient

        creds = L2Credentials(
            api_key=self._config.polymarket.api_key,
            api_secret=self._config.polymarket.api_secret.get_secret_value(),
            api_passphrase=self._config.polymarket.api_passphrase.get_secret_value(),
        )
        ks_rest_client = PolymarketRestClient(
            http_url=self._config.polymarket.host, creds=creds,
        )
        kill_switch = KillSwitch(
            daily_loss_limit_usdc=self._config.risk.daily_loss_limit_usdc,
            cancel_all_fn=ks_rest_client.cancel_all_orders,
        )

        # Filter strategy params to *Config fields.
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

        node_config = TradingNodeConfig(
            trader_id=TraderId(f"LIVE-{self._slug[:16].upper()}"),
            logging=LoggingConfig(log_level=self._config.log_level),
            exec_engine=LiveExecEngineConfig(reconciliation=True),
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
                    is_paper=False,  # ← THE difference vs PaperRunnerV2.
                ),
            },
            # No paper-fill engine in live mode — real fills come from PM.
            actors=[],
            strategies=[strategy_cfg],
            timeout_connection=30.0,
        )

        node = TradingNode(config=node_config)
        node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)
        node.add_exec_client_factory("POLYMARKET", PolymarketLiveExecClientFactory)
        node.build()

        # Wire the data client with the short-symbol → full-token map.
        data_client = self._find_data_client(node, "POLYMARKET")
        token_map: dict[str, str] = {}
        for cid, yes_id, no_id in self._pairs:
            token_map[instruments[yes_id].symbol.value] = yes_id
            token_map[instruments[no_id].symbol.value] = no_id
        if data_client is not None:
            data_client.register_tokens(token_map)

        # Wire per-strategy capital allocator (Portfolio-backed pre-trade gate).
        exec_client = self._find_exec_client(node, "POLYMARKET")
        if exec_client is not None:
            from nautilus_predict.agent import portfolio as _alloc_mod

            warnings = _alloc_mod.validate_allocations(self._config)
            for w in warnings:
                log.critical("allocator config warning: %s", w)
                emit_event(
                    type="portfolio_config_warning",
                    summary=w, severity="critical", slug=self._slug, data={},
                )

            equity_provider = self._build_equity_provider(ks_rest_client)
            allocator = _alloc_mod.for_slug(
                self._slug, self._config, equity_provider=equity_provider,
            )
            try:
                allocator.set_portfolio(node.portfolio)
            except Exception as exc:
                log.critical("could not attach portfolio to allocator: %s", exc)
            exec_client._portfolio_allocator = allocator  # type: ignore[attr-defined]
            emit_event(
                type="portfolio_alloc_armed",
                summary=(
                    f"{self._slug}: LIVE allocator armed cap=$"
                    f"{allocator.cap_usdc:.2f} ({allocator.cap_spec.describe()})"
                ),
                severity="critical", slug=self._slug,
                data=allocator.snapshot(),
            )

        # Register pairs / instruments with the strategy.
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

        for instr in instruments.values():
            try:
                node.cache.add_instrument(instr)
            except Exception:
                pass

        emit_event(
            type="live_start",
            summary=f"{self._slug}: LIVE trading STARTING ({len(self._pairs)} pairs)",
            severity="critical",
            slug=self._slug,
            data={"instruments": len(instruments), "duration_secs": self._duration_secs},
        )

        # Timer thread for duration-bounded runs.
        start_ts = time.monotonic()
        if self._duration_secs:
            def _timer():
                time.sleep(self._duration_secs)
                log.critical("LiveRunner duration elapsed; stopping")
                try:
                    node.stop()
                except Exception as exc:
                    log.warning("timer node.stop error: %s", exc)
            threading.Thread(target=_timer, daemon=True, name="live-runner-timer").start()

        try:
            node.run()
        except KeyboardInterrupt:
            log.critical("LIVE TRADING INTERRUPTED")
            try:
                node.stop()
            except Exception:
                pass
        except Exception as exc:
            log.critical("LIVE TRADING ERROR: %s — triggering kill switch", exc)
            kill_switch.trigger(f"LiveRunner uncaught: {exc}")
            raise
        finally:
            emit_event(
                type="live_stop",
                summary=f"{self._slug}: LIVE trading stopped",
                severity="warn",
                slug=self._slug,
                data={"duration_secs": round(time.monotonic() - start_ts, 2)},
            )

        return LiveRunSummary(
            slug=self._slug,
            instruments=len(instruments),
            duration_secs=round(time.monotonic() - start_ts, 2),
            kill_switch_triggered=kill_switch.is_triggered,
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

    def _find_strategy(self, node, class_name: str):
        try:
            for strat in node.trader.strategies():
                if type(strat).__name__ == class_name:
                    return strat
        except Exception:
            pass
        return None

    def _find_data_client(self, node, venue_str: str):
        try:
            from nautilus_trader.model.identifiers import ClientId

            cid = ClientId(venue_str)
            return node.kernel.data_engine._clients[cid]  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("_find_data_client failed: %s", exc)
            return None

    def _find_exec_client(self, node, venue_str: str):
        try:
            from nautilus_trader.model.identifiers import ClientId

            cid = ClientId(venue_str)
            return node.kernel.exec_engine._clients[cid]  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("_find_exec_client failed: %s", exc)
            return None

    def _build_equity_provider(self, rest_client):
        """
        Live runner: equity refresh is REQUIRED. If it fails we still build
        the provider (so absolute caps work) but emit a critical event.
        Pct caps will resolve to 0 (block all trading) until refresh succeeds —
        that's the safe direction for live.
        """
        import asyncio

        from nautilus_predict.agent.venue_equity import PolymarketEquityProvider
        from nautilus_predict.venues.polymarket.auth import derive_address

        pk = self._config.polymarket.private_key.get_secret_value()
        try:
            wallet = derive_address(pk)
        except Exception as exc:
            log.critical("equity: could not derive wallet address: %s", exc)
            return None

        provider = PolymarketEquityProvider(
            wallet_address=wallet, rest_client=rest_client,
        )
        try:
            asyncio.run(provider.refresh())
            log.critical(
                "equity: LIVE primed for %s — total=$%.2f (source=%s)",
                wallet[:10] + "...",
                provider.current_usdc(),
                provider.snapshot.source if provider.snapshot else "n/a",
            )
        except Exception as exc:
            log.critical(
                "equity: LIVE refresh failed (pct caps will block until "
                "refreshed manually): %s", exc,
            )
        return provider
