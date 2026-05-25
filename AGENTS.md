# AGENTS.md — Nautilus-Predict Codebase Guide

Authoritative documentation for any AI agent working in this repo. Read it
before making changes. Update the Phase Gate State table when a phase
verifies complete.

---

## Project Identity

**What it is:** An algorithmic trading system built on NautilusTrader targeting:
- **Polymarket** — binary prediction markets (primary venue, complement arb strategy)
- **Hyperliquid** — perpetual futures DEX (secondary, for hedging — not yet wired)

**Primary strategy:** Complement arbitrage on Polymarket binary markets. In
binary markets YES + NO shares must resolve to exactly $1.00. When the
combined cost of buying both falls below $1.00 minus fees, both legs +
hold-to-resolution = risk-free profit.

**Ultimate goal:** Self-managing agentic layer that proposes, backtests,
and deploys strategy variants autonomously. Foundations are in place — see
Phase 5 in `specs/2026-05-24_bootstrap.md` for the lifecycle/runbook design.

**Agentic architecture:** model-agnostic. The codebase exposes CLI tools
(`scripts/*.py` with JSON I/O) and decision runbooks (`runbooks/*.md`). Any
external agent runtime can drive a runbook. No `anthropic` SDK dependency.

---

## Canonical Module Map

### Configuration & node
| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/config.py` | Single source of truth. Always load via `load_config()`. Classes: `TradingConfig`, `PolymarketConfig`, `HyperliquidConfig`, `RiskConfig`, `MarketMakerConfig`, `ArbConfig`. |
| `src/nautilus_predict/node.py` | Builds a `TradingNode` for live mode (currently — paper mode uses the lightweight `PaperRunner` instead). |
| `src/nautilus_predict/main.py` | `--mode {paper,backtest,live}` entry. Paper selects pairs via the arb-complement hypothesis. |

### Strategies (canonical)
| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/strategies/base.py` | `NautilusPredictStrategy` — kill_switch-aware base. |
| `src/nautilus_predict/strategies/arb_complement.py` | **Canonical complement arb.** `BinaryArbStrategy` + `BinaryArbConfig`. Reads best ask directly from delta stream (cache.order_book isn't auto-maintained for delta-only subscriptions). |

### Data layer
| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/data/catalog.py` | PyArrow/Parquet time-series store. `write_trades`, `write_orderbook_snapshot`, `read_trades`, `read_orderbook_history`, `validate_dataset`, `list_available_markets`. Token-id dir names are the full 77-digit decimal. |
| `src/nautilus_predict/data/ingestion.py` | `PolymarketDataIngester`. `fetch_historical_trades(condition_id, ...)` uses `data-api.polymarket.com/trades?market=<cond>` with offset paging (~3500-record API cap, handled). `fetch_orderbook_snapshots` polls CLOB `/book` (forward-only — no historical book endpoint). |
| `src/nautilus_predict/data/market_catalog.py` | SQLite metadata store (`data/market_catalog.db`). `MarketCatalog`, `MarketRow`, `gamma_to_row` mapping helper. |
| `src/nautilus_predict/data/market_filter.py` | `MarketCriteria` + `select_markets(criteria, catalog)`. Frontmatter-friendly via `MarketCriteria.from_dict`. Post-filter `yes_prob_range` reads outcomePrices out of `raw_json`. |
| `src/nautilus_predict/data/parquet_loader.py` | **Parquet → NautilusTrader.** `make_instrument(token_id, condition_id)` builds a `BettingInstrument`; `load_trades_as_trade_ticks`, `load_book_as_order_book_deltas`, `reconstruct_book_from_trades` for backtest data feed. |

### Venues
| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/venues/polymarket/auth.py` | EIP-712 + HMAC-SHA256. `sign_l2_request`, `derive_address`, `derive_api_key`, `L2Credentials`. |
| `src/nautilus_predict/venues/polymarket/client.py` | `PolymarketRestClient` (aiohttp) + `PolymarketWsClient` (reconnect with backoff). |
| `src/nautilus_predict/venues/polymarket/gamma.py` | `GammaClient` — public metadata API (`gamma-api.polymarket.com`). |
| `src/nautilus_predict/venues/polymarket/data.py`, `execution.py`, `factory.py` | NT `LiveMarketDataClient` / `LiveExecutionClient` scaffolding. NOT wired into PaperRunner yet (PaperRunner bypasses NT TradingNode for now). |
| `src/nautilus_predict/venues/polymarket/orders.py` | Order-build helpers (EIP-712 limit order signing). |
| `src/nautilus_predict/venues/hyperliquid/` | Hyperliquid adapter scaffolding — not wired into any active strategy. |

### Risk
| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/risk/kill_switch.py` | `KillSwitch` — persists triggered state to `data/.kill_switch` (atomic temp+rename). Refuses to start if a prior process tripped the flag. |
| `src/nautilus_predict/risk/heartbeat.py` | `HeartbeatWatcher` — trips the kill switch on connection timeout. |
| `src/nautilus_predict/risk/position_limits.py` | `PositionLimits` — per-market USDC caps. |

### Runners
| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/runner/backtest.py` | `BacktestRunner.run_pair / run_hypothesis`. NautilusTrader `BacktestEngine` with `FillModel(prob_fill_on_limit=0.5, prob_slippage=0.5)` and 200ms `LatencyModel`. Terminal PnL is the genuine arb edge: matched pairs resolve at $1.00. |
| `src/nautilus_predict/runner/paper.py` | `PaperRunner` — in-process WS stream + simulated fills + jsonl trade log. **Doesn't go through NT TradingNode** (that's still TODO when execution.py/data.py are fully wired). |
| `src/nautilus_predict/runner/live.py` | `LiveRunner` — placeholder, blocked on Phase 4. |

### Agentic layer (Phase 5)
| Module | Purpose |
|--------|---------|
| `src/nautilus_predict/agent/lifecycle.py` | **The only writer** to `lifecycle_transitions` + `hypotheses.state`. `add_hypothesis`, `transition`, `history`, `record_experiment`, `list_experiments`. Atomic via `BEGIN IMMEDIATE`. |
| `src/nautilus_predict/agent/codegen_guards.py` | AST import-allowlist + lookahead heuristic. `check_file`, `check_source`. |
| `src/nautilus_predict/agent/budget.py` | Daily token / backtest / paper-start / live-start counters in `budget_ledger`. |
| `research/hypotheses/<slug>.md` | YAML frontmatter (market_criteria + strategy class refs) + body. `propose_hypothesis.py` registers these into the DB. |
| `research/experiments.db` | SQLite. Tables: `hypotheses`, `experiments`, `lifecycle_transitions`, `budget_ledger`. |
| `runbooks/*.md` | Agent-facing instructions. `onboard-existing-strategy.md`, `test-strategy.md`, `codegen-strategy.md`. |

**Deleted (do not recreate):**
- `src/nautilus_predict/adapters/` — was dead code; `venues/` is canonical
- `src/nautilus_predict/strategies/complement_arb.py` — had constructor mismatch

---

## Agent CLI Surface

Every script under `scripts/` is designed for agentic use: argparse + JSON on stdout, exit 0/non-zero.

| Script | What it does |
|--------|--------------|
| `scripts/check_env.py` | Validate env vars + connectivity. |
| `scripts/fetch_markets.py` | List active Polymarket markets via CLOB. |
| `scripts/sync_market_metadata.py` | Pull gamma metadata into `data/market_catalog.db`. `--full` for everything. |
| `scripts/download_polymarket_data.py --condition-id <id> --start --end` | Fetch trade history into Parquet. |
| `scripts/backtest.py --hypothesis-slug <slug>` (or `--condition-id+--yes/no-token-id`) | NautilusTrader backtest. |
| `scripts/research_cli.py {init,list,show,history,experiments,budget}` | Read-only inspector for experiment DB. |
| `scripts/propose_hypothesis.py --file <md>` | Register hypothesis MD into DB. |
| `scripts/transition_lifecycle.py --slug --to --reason` | Sole atomic-write entry for state transitions. Human-gated ones refuse non-`user:*` actors. |
| `scripts/smoke_test_strategy.py --slug <slug>` | AST guards + optional pytest + snapshot to `research/snapshots/<hash>.py`. |
| `scripts/eval_strategy.py --slug --start --end` | Run hypothesis backtest, record experiment, apply decision rules. |
| `scripts/halt_trading.py --reason <text>` | Write `data/.kill_switch` — halts all paper/live runners. |
| `scripts/reset_kill_switch.py --confirm` | Clear `data/.kill_switch`. Refuses without `--confirm`. |
| `scripts/derive_polymarket_keys.py` | One-time L2 credential derivation. |

---

## What Agents Must Never Do

1. **Touch `.env` directly.** Suggest changes via comments in `.env.example` only.
2. **Bypass the kill switch.** Don't short-circuit `_check_kill_switch` or remove `read_flag` checks.
3. **Create a second implementation of an existing strategy.** Delete the old one first, or use `parent_slug` to fork.
4. **Commit credentials.** `.gitignore` already covers `.env`, `data/.kill_switch`, `data/market_catalog.db`, `data/parquet/*`, etc.
5. **Promote past human gates.** `PAPER_READY → PAPER` and `LIVE_READY → LIVE` require `--actor user:*`. The transition script enforces this; don't paper over it.
6. **Skip smoke for agent-written strategies.** Always invoke `scripts/smoke_test_strategy.py` before `transition_lifecycle.py --to SMOKE_PASS`.
7. **Edit a registered strategy file in place.** If parameters change, create a new slug with `parent_slug` pointing back. Rejection memory depends on this invariant.

---

## Phase Gate State

| Phase | Status | Notes |
|-------|--------|-------|
| **Phase 0: Foundation** | ✅ Complete | Config + node + base strategy clean. |
| **Phase 0.5: uv environment** | ✅ Complete | `.venv` via uv; `make check-env` green. |
| **Phase 0.6: Persistent KillSwitch** | ✅ Complete | `data/.kill_switch` + `halt_trading.py` / `reset_kill_switch.py`. |
| **Phase 1: Data Infrastructure** | ✅ Complete | Historical trades + book snapshots + validate_dataset. ~3500 trade cap per condition (data-api offset limit). |
| **Phase 1.6: Market Metadata** | ✅ Complete | Gamma + MarketCatalog + MarketCriteria + `select_markets()`. Seed `research/hypotheses/arb-complement.md`. |
| **Phase 2: Backtesting** | ✅ Complete | `BacktestRunner` wired to NT engine with FillModel + LatencyModel. Hypothesis-driven entry point. US-Iran market backtest: +$13.86 over 97 paired arbs ($0.14/arb). |
| **Phase 3: Paper Trading** | 🟡 Lightweight harness | `PaperRunner` streams live WS, simulates fills, logs to `logs/paper_trades_<date>.jsonl`. Full NT TradingNode wiring deferred until `venues/polymarket/execution.py` / `data.py` complete. |
| **Phase 4: Live Trading** | ❌ Not started | Blocked on Phase 3 full TradingNode + 24h paper run. |
| **Phase 5: Agentic Layer** | 🟢 Foundation in place | Lifecycle DB + CLI + codegen guards + 3 runbooks. Discovery loop + walk-forward optimisation are next. |

Update this table when a phase status changes.

---

## How to Run Tests and Verify Changes

```bash
make dev                            # one-time: uv venv + deps
make test                           # 80+ tests via pytest
make lint                           # ruff
make check-env                      # env + connectivity check

# Data + metadata
make sync-markets                   # gamma → data/market_catalog.db
make download-data CONDITION_ID=0x..  # trade history into Parquet
make sync-markets-full              # full sync (slower)

# Backtest
.venv/bin/python scripts/backtest.py --hypothesis-slug arb-complement \
    --start 2026-05-10 --end 2026-05-26

# Paper trading (live WS, simulated fills, 60s timed run)
.venv/bin/python -m nautilus_predict.main --mode paper --duration-secs 60

# Agentic flow
.venv/bin/python scripts/research_cli.py init
.venv/bin/python scripts/propose_hypothesis.py \
    --file research/hypotheses/arb-complement.md --initial-state BACKTEST
.venv/bin/python scripts/eval_strategy.py \
    --slug arb-complement --start 2026-05-10 --end 2026-05-26
.venv/bin/python scripts/research_cli.py show --slug arb-complement
```

---

## Key Things to Know Before Editing

### Config attribute names (authoritative)
| Class | Attribute | Env var |
|---|---|---|
| `PolymarketConfig` | `host` | `POLY_HOST` |
| `PolymarketConfig` | `ws_host` | `POLY_WS_HOST` |
| `PolymarketConfig` | `exchange_address` | `POLY_EXCHANGE_ADDRESS` |
| `HyperliquidConfig` | `api_url` | `HL_API_URL` |
| `HyperliquidConfig` | `ws_url` | `HL_WS_URL` |
| `ArbConfig` | `min_profit_usdc` | `ARB_MIN_PROFIT_USDC` |
| `ArbConfig` | `max_capital_usdc` | `ARB_MAX_CAPITAL_USDC` |

### Strategy wiring (BinaryArbStrategy)
- Config: `BinaryArbConfig` (frozen `StrategyConfig`). Knobs: `min_profit_usdc`, `max_capital_usdc`, `order_notional_usdc`, `allow_concurrent`, `taker_fee` (Polymarket is currently zero-fee on binary takers — default 0.0).
- Register pairs via `register_market_pair(condition_id, yes_id, no_id)`. Calling pre-start queues into `_pending_pairs` (flushed in `on_start`).
- Best-ask is read directly from the incoming `OrderBookDeltas` payload — NT does not auto-maintain a delta-only book in the cache.
- Aborts via `ClientOrderId` lookup (not `cache.client_order_id` — that wants a `VenueOrderId`).

### Token-id <-> instrument-id mapping
- `make_instrument(token_id, condition_id)` builds a `BettingInstrument` with `selection_id` = sha1(token_id)[:31bits], `market_id` = first 12 hex of condition_id. Symbol = first 24 decimal digits.
- Catalog dir names = full 77-digit token_id (no truncation; rooted at `data/parquet/{trades,orderbooks}/<token>/`).

### Backtest realism knobs
- `FillModel(prob_fill_on_limit=0.5, prob_slippage=0.5)` — pessimistic by default.
- `LatencyModel(base_latency_nanos=200_000_000)` — 200ms round-trip realistic for PM via aiohttp.
- Trade-print–reconstructed book has only 1 level per side; trades aren't a substitute for snapshots when measuring true arb depth.

### Auth flow (Polymarket)
- L1: EIP-712 (domain ClobAuth, chainId 137) → one-shot, derives L2 creds.
- L2: HMAC-SHA256 on every authenticated request.
- L1 auth headers use underscore separators (`POLY_ADDRESS`, `POLY_SIGNATURE`, etc.).
- One-time setup: `.venv/bin/python scripts/derive_polymarket_keys.py`.

### Lifecycle invariants
- Every state change goes through `lifecycle.transition()` (or `scripts/transition_lifecycle.py`).
- `HUMAN_GATED = {(PAPER_READY, PAPER), (LIVE_READY, LIVE)}` — actor must start with `user:`.
- `record_experiment()` is the only insert path for `experiments`. Always include `code_hash` once smoke is wired into eval.
- Edits to a registered strategy file → new slug with `parent_slug`. Old slug stays in its terminal state.
