# AGENTS.md — Nautilus-Predict Codebase Guide

This file is authoritative documentation for any AI agent working in this repo.
Read it before making changes. Update the Phase Gate State section when a phase is verified complete.

---

## Project Identity

**What it is:** An algorithmic trading system built on NautilusTrader targeting:
- **Polymarket** — binary prediction markets (primary venue, complement arb strategy)
- **Hyperliquid** — perpetual futures DEX (secondary, for hedging)

**Primary strategy:** Complement arbitrage on Polymarket binary markets.
In binary markets, YES + NO shares must resolve to exactly $1.00. When the
combined cost of buying both falls below $1.00 minus the 2% taker fee, buying
both guarantees a risk-free profit at resolution.

**Ultimate goal:** Self-managing agentic layer that proposes, backtests, and
deploys strategy variants autonomously. Not yet started — see Phase 5.

**Agentic architecture (Phase 5):** model-agnostic. The codebase exposes CLI
tools (`scripts/*.py` with JSON I/O) and decision runbooks (`runbooks/*.md`).
Any external agent runtime — Claude Code, another LLM, or a human operator —
can be pointed at a runbook to execute it. No `anthropic` SDK dependency in
the codebase.

---

## Canonical Module Map

| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/config.py` | Single source of truth for all config. Always load via `load_config()`. Classes: `TradingConfig`, `PolymarketConfig`, `HyperliquidConfig`, `RiskConfig`, `MarketMakerConfig`, `ArbConfig`. |
| `src/nautilus_predict/node.py` | Builds a `TradingNode` for live/paper modes. Entry point for `build_node()`. |
| `src/nautilus_predict/strategies/arb_complement.py` | **Canonical complement arb strategy.** `BinaryArbStrategy` + `BinaryArbConfig`. Register pairs via `register_market_pair()` before trading. |
| `src/nautilus_predict/strategies/market_maker.py` | Passive market-making. `PolymarketMarketMaker` takes `(token_id, params, config)`. Not wired to runners yet. |
| `src/nautilus_predict/risk/kill_switch.py` | `KillSwitch` — halts all trading when daily loss limit is breached. Always active in paper/live. |
| `src/nautilus_predict/risk/heartbeat.py` | `HeartbeatWatcher` — triggers kill switch on connection timeout. Always active. |
| `src/nautilus_predict/risk/position_limits.py` | `PositionLimits` — enforces per-market USDC caps. |
| `src/nautilus_predict/venues/polymarket/auth.py` | Functional auth API: `sign_l2_request()`, `derive_address()`, `sign_eip712_message()`, `derive_api_key()`. `L2Credentials` dataclass. |
| `src/nautilus_predict/venues/polymarket/client.py` | `PolymarketRestClient` (aiohttp, authenticated REST) + `PolymarketWsClient` (WebSocket with reconnect). |
| `src/nautilus_predict/venues/polymarket/execution.py` | NautilusTrader `LiveExecutionClient` for Polymarket. Partially stubbed (Phase 3). |
| `src/nautilus_predict/venues/polymarket/data.py` | NautilusTrader `LiveMarketDataClient` for Polymarket. Partially stubbed (Phase 3). |
| `src/nautilus_predict/venues/polymarket/factory.py` | **Complete.** `PolymarketLiveDataClientFactory` + `PolymarketLiveExecClientFactory`. |
| `src/nautilus_predict/venues/hyperliquid/` | Hyperliquid adapter — partially implemented. |
| `src/nautilus_predict/data/catalog.py` | **Complete.** PyArrow/Parquet storage. `write_trades()`, `read_orderbook_history()`, `list_available_markets()`. |
| `src/nautilus_predict/data/ingestion.py` | `PolymarketDataIngester` — historical fetch is stubbed (Phase 1), `run_continuous()` is implemented. |
| `src/nautilus_predict/runner/backtest.py` | `BacktestRunner` — BacktestEngine wiring is TODO (Phase 2). |
| `src/nautilus_predict/runner/paper.py` | `PaperRunner` — risk/heartbeat wired; TradingNode wiring is TODO (Phase 3). |
| `src/nautilus_predict/runner/live.py` | `LiveRunner` — risk/heartbeat wired; TradingNode wiring is TODO (Phase 4). |

**Deleted (do not recreate):**
- `src/nautilus_predict/adapters/` — was dead code; `venues/` is canonical
- `src/nautilus_predict/strategies/complement_arb.py` — had constructor mismatch; `arb_complement.py` is canonical

---

## What Agents Must Never Do

1. **Touch `.env` directly.** It contains real secrets. Suggest changes via comments in `.env.example` only.
2. **Bypass the kill switch or remove safety checks in runners.** The double opt-in (`LIVE_TRADING_CONFIRMED=true`) is mandatory.
3. **Create a second implementation of the arb strategy.** One canonical file: `arb_complement.py` / `BinaryArbStrategy`. Delete old before creating new.
4. **Commit credentials or secrets.** The `.gitignore` must stay as-is.
5. **Enable live trading or submit real orders.** `LIVE_TRADING_CONFIRMED=true` is user-only. Never set it programmatically.
6. **Recreate adapters/.** It has been deliberately deleted. All venue integration is in `venues/`.

---

## Phase Gate State

| Phase | Status | Blocker |
|-------|--------|---------|
| **Phase 0: Foundation** | ✅ Complete | None — all imports clean, tests pass |
| **Phase 0.5: Python Environment** | ✅ Complete | `.venv` bootstrapped via uv; `make check-env` green |
| **Phase 1: Data Infrastructure** | 🟡 In progress | Step 1.1 (endpoint discovery) done; 1.2 (historical fetch) is next |
| **Phase 2: Backtesting** | ❌ Not started | Blocked on Phase 1 data + `parquet_loader.py` |
| **Phase 3: Paper Trading** | ❌ Not started | Blocked on Phase 1; needs `execution.py` and `data.py` WS handlers implemented |
| **Phase 4: Live Trading** | ❌ Not started | Blocked on Phase 3 |
| **Phase 5: Agentic Layer** | ❌ Not started | Blocked on Phase 2 (agent's primary tool is `make backtest`) |

**Update this table when a phase is verified complete.**

---

## How to Run Tests and Verify Changes

Default workflow is uv-managed virtualenv (`.venv/`). Docker is an option for
NautilusTrader runtime execution (paper/live), but tests and lint run locally.

```bash
# One-time: create .venv and install deps via uv
make dev

# Run full test suite (inside .venv)
make test

# Run linter (ruff + mypy)
make lint

# Check env config + connectivity (credentials optional)
make check-env

# Derive Polymarket L2 API credentials from the wallet private key
.venv/bin/python scripts/derive_polymarket_keys.py

# Run backtest (requires Phase 1 data)
make backtest

# Run paper trading (requires credentials in .env)
make paper
```

Docker targets (`make docker-build`, `make docker-up`, etc.) remain available
for containerized NautilusTrader runtime — useful for paper/live deployments
but not required for development.

Config verification:
```bash
.venv/bin/python -c "from nautilus_predict.config import load_config; cfg = load_config(); print(cfg.polymarket.host, cfg.arb.min_profit_usdc)"
```

---

## Key Things to Know Before Editing

### Config attribute names (authoritative)
| Config class | Attribute | Env var |
|---|---|---|
| `PolymarketConfig` | `host` | `POLY_HOST` |
| `PolymarketConfig` | `ws_host` | `POLY_WS_HOST` |
| `PolymarketConfig` | `exchange_address` | `POLY_EXCHANGE_ADDRESS` |
| `HyperliquidConfig` | `api_url` | `HL_API_URL` |
| `HyperliquidConfig` | `ws_url` | `HL_WS_URL` |
| `HyperliquidConfig` | `account_address` | `HL_ACCOUNT_ADDRESS` |
| `MarketMakerConfig` | `spread_bps` | `MM_SPREAD_BPS` |
| `MarketMakerConfig` | `order_size_usdc` | `MM_ORDER_SIZE_USDC` |
| `ArbConfig` | `min_profit_usdc` | `ARB_MIN_PROFIT_USDC` |
| `ArbConfig` | `max_capital_usdc` | `ARB_MAX_CAPITAL_USDC` |

### Strategy wiring
- `BinaryArbStrategy` takes `BinaryArbConfig` (a NautilusTrader `StrategyConfig`, frozen dataclass)
- Register market pairs AFTER construction via `strategy.register_market_pair(condition_id, yes_id, no_id)`
- Kill switch is NOT a constructor parameter — it lives in `PositionLimits`/`KillSwitch` outside the strategy

### Auth flow
- L1 (on-chain): `sign_eip712_message()` — Ethereum EIP-712 (domain `ClobAuthDomain`, chainId 137, nonce typed as `uint256`), used once to derive L2 creds
- L2 (off-chain): `sign_l2_request()` — HMAC-SHA256, used for every REST/WS request
- L1 auth headers use underscore separators (`POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_NONCE`) — nginx-style hyphen variants are rejected
- Pre-generate L2 creds with `.venv/bin/python scripts/derive_polymarket_keys.py` and store in `.env`. The script tries `GET /auth/derive-api-key` first, then falls back to `POST /auth/api-key` for first-time creation.

### Risk constants
- `TAKER_FEE = 0.02` in `arb_complement.py` — 2% taker fee on Polymarket
- `RiskConfig.daily_loss_limit_usdc` must be negative (e.g. -200.0)
- Kill switch halts via `cancel_all_fn` which calls `PolymarketRestClient.cancel_all_orders()`
