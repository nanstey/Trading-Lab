# AGENTS.md — Trading Lab Codebase Guide

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
| `src/nautilus_predict/config.py` | Single source of truth. Loads `.env` (secrets) + `config/system.yaml` + `config/venues.yaml` + `config/portfolio.yaml`. Use `load_config()`. New paths: `cfg.venues.polymarket.http_url`, `cfg.portfolio.risk.daily_loss_limit_usdc`, `cfg.system.watcher.single_day_limit_pct`. Legacy compat properties (`cfg.polymarket.host`, `cfg.risk.daily_loss_limit_usdc`) preserved. **No `trading_mode` / `TRADING_MODE`** — paper-vs-live is per-strategy via hypothesis state. |
| `config/system.yaml` | Log level, watcher thresholds, heartbeat timeout, budget caps. Committed. |
| `config/venues.yaml` | Polymarket + Hyperliquid + Polygon endpoints + contract addresses. Constants. |
| `config/portfolio.yaml` | Risk envelope + (future) per-strategy capital allocations. |
| `.env` | **Secrets only**. `POLY_PRIVATE_KEY`, `POLY_API_KEY/SECRET/PASSPHRASE`, `HL_PRIVATE_KEY/ACCOUNT_ADDRESS`, `LIVE_TRADING_CONFIRMED`. Gitignored. |
| `src/nautilus_predict/node.py` | Legacy `build_node(is_paper)` factory. Superseded — modern runners build their own TradingNode inline. |
| `src/nautilus_predict/main.py` | Stub — prints pointers to `scripts/paper_run_v2.py` etc. The old `--mode paper/live` flag has no behaviour beyond informational. |

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
| `src/nautilus_predict/runner/backtest.py` | `BacktestRunner.run_pair / run_hypothesis`. NautilusTrader `BacktestEngine` with `FillModel(prob_fill_on_limit=0.5, prob_slippage=0.5)` and 200ms `LatencyModel`. Terminal PnL is the genuine arb edge; per-pair Sharpe computed from realised pair-PnL series. |
| `src/nautilus_predict/runner/paper_v2.py` | **`PaperRunnerV2`** — real NT TradingNode + `is_paper=True` + `PolymarketPaperFillEngine` Actor. Production code path. |
| `src/nautilus_predict/runner/live_v2.py` | **`LiveRunner`** — same TradingNode as PaperRunnerV2, `is_paper=False`. Pre-flight refuses without TRADING_MODE=live, LIVE_TRADING_CONFIRMED=true, L1+L2 creds, kill switch clear, hypothesis state=LIVE. |
| `src/nautilus_predict/runner/generic_paper.py` | `GenericPaperRunner` — **legacy**. Monkey-patches order_factory; doesn't exercise real exec path. Available via `make paper-run-legacy`. |
| `src/nautilus_predict/runner/paper.py` | `PaperRunner` — older arb-specific in-process harness. Superseded; kept for reference. |
| `src/nautilus_predict/runner/live.py` | `LiveRunner` (old placeholder) — superseded by `live_v2.py`. |

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
| `scripts/optimize_strategy.py --slug --data-start --data-end [--workers N]` | Grid sweep + walk-forward (parallel); transitions to PAPER_READY / SHELVED / REJECTED. |
| `scripts/paper_run_v2.py --slug --duration-secs` | **Primary paper runtime** — real TradingNode + is_paper=True + fill engine. |
| `scripts/paper_run.py --slug --duration-secs` | Legacy GenericPaperRunner — monkey-patched harness, no real exec path. |
| `scripts/live_run.py --slug --duration-secs [--i-understand-this-is-live]` | LIVE trading. Default mode = pre-flight check ONLY; requires `--i-understand-...` to actually submit orders. |
| `scripts/run_ingestion.py` | Continuous WS data ingestion daemon — fills the catalog for rolling-eval. |
| `scripts/rolling_eval.py [--window-days N]` | Re-eval each PAPER strategy on the last N days. Cron entry; emits events. |
| `scripts/paper_summary.py --slug [--date]` | Pair entry/close signals → realised PnL report + experiments row. |
| `scripts/paper_watcher.py` | Auto-retirement (single-day -5% → HALTED, 7d -15% → RETIRED). |
| `scripts/operator_briefing.py [--md]` | Read events log + apply forwarding policy → JSON for external SMS agent. |
| `scripts/discover_strategies.py [--rss]` | Drain manual_inbox + (opt) RSS into PROPOSED. |
| `scripts/validate_loop.py` | Phase 5.11 — drives known-bad + known-good through the loop. |
| `scripts/halt_trading.py --reason <text>` | Write `data/.kill_switch` — halts all paper/live runners. |
| `scripts/reset_kill_switch.py --confirm` | Clear `data/.kill_switch`. Refuses without `--confirm`. |
| `scripts/derive_polymarket_keys.py` | One-time L2 credential derivation. |

### Runtime: TradingNode-driven paper + live (current)

Paper and live trading both run through a real NautilusTrader
`TradingNode` built via `runner/paper_v2.py` (`PaperRunnerV2`) and
`runner/live_v2.py` (`LiveRunner`). The ONLY difference between the two
runtimes is the `is_paper` flag on `PolymarketExecClientConfig`:

  - `is_paper=True` — `PolymarketExecutionClient` accepts orders and
    delegates fills to `PolymarketPaperFillEngine` (an Actor sitting on
    the same message bus). No venue calls; no money moves.
  - `is_paper=False` — same code path, but the execution client actually
    POSTs orders to PM and gets real fill confirmations via the
    user-channel WS.

That symmetry is the architectural commitment. There's no "paper
worked but live blew up" surprise possible — same NT engine, same
msgbus, same execution-client class.

Legacy `runner/generic_paper.py` (`GenericPaperRunner`) is preserved
for reference and as a fallback. It monkey-patches `order_factory` and
intercepts `submit_order` — does NOT exercise the real execution path.
Not used by default; available via `make paper-run-legacy`.

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

### Config paths (authoritative)
| Setting | New path | Lives in |
|---|---|---|
| Polymarket HTTP base | `cfg.venues.polymarket.http_url` | `config/venues.yaml` |
| Polymarket market WS | `cfg.venues.polymarket.ws_market_url` | `config/venues.yaml` |
| Polymarket user WS | `cfg.venues.polymarket.ws_user_url` | `config/venues.yaml` |
| Polymarket exchange addr | `cfg.venues.polymarket.exchange_address` | `config/venues.yaml` |
| Hyperliquid API | `cfg.venues.hyperliquid.api_url` | `config/venues.yaml` |
| Polygon RPC | `cfg.venues.polygon.rpc_url` | `config/venues.yaml` |
| Polymarket L1 key | `cfg.polymarket_secrets.private_key` | `.env` (`POLY_PRIVATE_KEY`) |
| Polymarket L2 creds | `cfg.polymarket_secrets.api_key/secret/passphrase` | `.env` (`POLY_*`) |
| Hyperliquid L1 key | `cfg.hyperliquid_secrets.private_key` | `.env` (`HL_PRIVATE_KEY`) |
| Daily loss limit | `cfg.portfolio.risk.daily_loss_limit_usdc` | `config/portfolio.yaml` |
| Max position USDC | `cfg.portfolio.risk.max_position_usdc` | `config/portfolio.yaml` |
| Heartbeat timeout | `cfg.system.heartbeat_timeout_secs` | `config/system.yaml` |
| Watcher thresholds | `cfg.system.watcher.*` | `config/system.yaml` |
| Daily budget caps | `cfg.system.budget.*` | `config/system.yaml` |
| Live opt-in gate | `live_trading_confirmed()` | `.env` (`LIVE_TRADING_CONFIRMED`) |

**Strategy params** are NOT system config — they live in the hypothesis MD frontmatter (`*Config(StrategyConfig)` defaults) + optimised winner row in `research/experiments.db`. `paper_run_v2.py` / `live_run.py` pick the winner automatically.

Legacy compat properties (`cfg.polymarket.host`, `cfg.risk.daily_loss_limit_usdc`) still work but are deprecated — prefer the new paths above.

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
