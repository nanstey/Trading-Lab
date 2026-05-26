# Implementation Roadmap

> **⚠ Status (2026-05-25):** Phases 0–5 of this roadmap are now ✅. The
> live `[ ]` boxes below are the older granular spec items that have
> been superseded or completed under different names. The **single
> authoritative status snapshot** is the table in [README.md](../README.md)
> and the Progress block in [specs/2026-05-24_bootstrap.md](../specs/2026-05-24_bootstrap.md).
> This file is kept for the rationale/criteria descriptions per phase,
> not as a checklist.

## Overview

Nautilus-Predict follows a disciplined phase-gate approach:
research → build → backtest → paper → live. Each phase has clear
success criteria that must be met before progressing to the next.

---

## Phase 0: Foundation

**Status: ✅ Complete**

### Objectives
- Establish project structure and architecture
- Define all adapter interfaces and data types
- Implement risk module with kill switch, heartbeat, and position limits
- Set up test framework and CI pipeline
- Document authentication flows

### Deliverables
- [x] `src/nautilus_predict/` package consolidated (adapters/ deleted; venues/ canonical)
- [x] Polymarket auth (EIP-712 + HMAC-SHA256) — `venues/polymarket/auth.py`
- [x] `KillSwitch`, `HeartbeatWatcher`, `PositionLimits` risk modules
- [x] `DataCatalog` with PyArrow/Parquet storage
- [x] `polyfill-rs` Rust crate skeleton
- [x] `BacktestRunner`, `PaperRunner`, `LiveRunner` stubs
- [x] Test suite for risk and auth modules
- [x] Docker and docker-compose configuration
- [x] `AGENTS.md` authoritative agent guide
- [x] Single canonical complement-arb implementation (`BinaryArbStrategy`)
- [x] Config attribute mismatches in `node.py` fixed

### Success Criteria — Met
- All tests pass: `make test`
- `check_env.py` runs cleanly without credentials
- Code passes linting: `make lint`

---

## Phase 0.5: Python Environment

**Status: ✅ Complete**

### Deliverables
- [x] `uv`-managed `.venv/` with Python 3.12
- [x] `Makefile` updated: `PYTHON := .venv/bin/python3`, `venv` target, `dev` target uses uv
- [x] `.gitignore` includes `.venv/`
- [x] `make check-env` returns 23/23 green (connectivity via `data-api.polymarket.com`)

---

## Phase 1: Data Infrastructure

**Status: 🟡 In Progress**
**Estimated: 2-3 weeks remaining**

### Objectives
- Ingest and store historical Polymarket market data
- Build a queryable Parquet data catalog
- Create replay tooling for strategy research

### Deliverables
- [x] Step 1.1 — Confirmed Polymarket data endpoints (`data-api.polymarket.com`)
- [ ] `PolymarketDataIngester.fetch_historical_trades()` implemented
- [ ] `scripts/download_polymarket_data.py` working end-to-end
- [ ] Historical data for 10+ markets covering 3+ months
- [ ] `DataCatalog.read_orderbook_history()` with time-range queries
- [ ] Data validation: check for gaps, stale data, schema mismatches
- [ ] Continuous ingestion via WebSocket (`run_continuous()`)

### Success Criteria
- `python scripts/download_polymarket_data.py --token-id <id> --start 2024-01-01` completes
- Parquet files are written to `./data/parquet/`
- `DataCatalog.list_available_markets()` returns expected tokens
- Data is queryable with correct time ranges

---

## Phase 2: Backtesting

**Status: Not Started**
**Estimated: 3-4 weeks**

### Objectives
- Implement complement arb strategy fully
- Run complement arb backtest on historical data
- Calibrate market maker parameters
- Validate risk module integration

### Deliverables
- [ ] `BinaryArbStrategy` fully implemented (currently has stub logic in `arb_complement.py`)
- [ ] `BacktestRunner.run()` fully wired to NautilusTrader `BacktestEngine`
- [ ] `parquet_loader.py` — Parquet → `TradeTick` adapter for NautilusTrader
- [ ] Complement arb backtest on 90 days of data
- [ ] Market maker backtest with simulated fee model
- [ ] Performance analytics: Sharpe ratio, max drawdown, fill rate
- [ ] Backtest configuration YAML files

### Success Criteria
- Complement arb strategy shows positive expected value on historical data
- Kill switch correctly halts backtest when loss limit is reached
- `make backtest` runs end-to-end without errors
- Sharpe ratio and drawdown statistics are generated

---

## Phase 3: Paper Trading

**Status: Not Started**
**Estimated: 2-3 weeks**

### Objectives
- Connect to live Polymarket feeds in paper mode
- Validate strategy signal quality vs. backtest expectations
- Measure end-to-end latency
- Test risk module under realistic conditions

### Deliverables
- [ ] `PaperRunner.run()` fully implemented with NautilusTrader TradingNode
- [ ] Live WebSocket subscriptions working
- [ ] Paper fills being generated and logged
- [ ] Latency measurements: signal → simulated fill
- [ ] `HeartbeatWatcher` tested with intentional connection drops
- [ ] Kill switch tested with simulated loss scenarios
- [ ] 1-week paper trading run with daily PnL reports

### Success Criteria
- Paper trading runs continuously for 24+ hours without crashes
- Signal-to-fill latency < 500ms (Python path)
- Heartbeat timeout triggers kill switch correctly
- Daily PnL tracking matches expected strategy returns

---

## Phase 4: Live Trading

**Status: Not Started**
**Estimated: 2-4 weeks preparation + gradual ramp-up**

### Objectives
- Deploy to production with real (small) capital
- Implement monitoring and alerting
- Ramp position limits gradually
- Achieve stable profitable operation

### Deliverables
- [ ] `LiveRunner.run()` fully implemented
- [ ] Polymarket ExecutionClient integrated with NautilusTrader
- [ ] polyfill-rs Rust hot path compiled and integrated
- [ ] Hosting decision made (see specs/2026-05-24_bootstrap.md § Deployment Posture — currently deferred)
- [ ] Real-time monitoring dashboard (logs + PnL)
- [ ] Alert on: kill switch trigger, heartbeat failure, large fills
- [ ] `LIVE_TRADING_CONFIRMED` double opt-in verified
- [ ] Initial capital: $100 USDC
- [ ] Ramp to $500 USDC after 1 week of profitable paper
- [ ] Ramp to $5,000 USDC after 1 month of profitable live

### Success Criteria (Gatekeeping)
- 1 week of live trading at $100 without kill switch trigger
- Daily PnL variance within backtest expectations (±2 sigma)
- Cancel/replace latency < 100ms (Rust path)
- System uptime > 99% (heartbeat no timeouts)

### Pre-Live Checklist
- [ ] Credentials stored in `.env` (never committed to git)
- [ ] `MAX_POSITION_USDC` set to $10.0 for initial test
- [ ] `DAILY_LOSS_LIMIT_USDC` set to -$50.0 for initial test
- [ ] Polymarket account funded with test USDC amount
- [ ] `scripts/check_env.py` runs cleanly
- [ ] `make paper` runs for 24h without issues

---

## Phase 5: Agentic Layer

**Status: Not Started**
**Estimated: 2-3 weeks**

### Objectives
- Build a model-agnostic agentic interface — CLI tools + decision runbooks
- Any external agent runtime (Claude Code, another LLM, or a human operator) can drive the system by following a runbook
- No `anthropic` SDK dependency in the codebase

### Design principle
The codebase provides composable tools and decision playbooks. The agent runtime is external and pluggable.

### Deliverables
- [ ] CLI tool surface: `scripts/eval_strategy.py`, `scripts/list_markets.py`, `scripts/promote_config.py`, `scripts/get_live_pnl.py`, `scripts/halt_trading.py` — all stateless, JSON I/O, predictable exit codes
- [ ] `src/nautilus_predict/agent/evaluator.py` — pure-Python grid search (no LLM)
- [ ] `runbooks/strategy-evaluator.md` — parameter sweep + promotion rules
- [ ] `runbooks/live-anomaly-watcher.md` — PnL monitoring + escalation
- [ ] `runbooks/new-market-onboarding.md` — new-market viability check
- [ ] Optional: `.claude/skills/*` thin wrappers for slash-command invocation

### Success Criteria
- An external agent pointed at `runbooks/strategy-evaluator.md` runs a parameter sweep, applies the promotion decision rule, and produces a human-reviewable report
- No `anthropic` import anywhere in the codebase
- Each runbook is self-contained (any agent with no prior context can execute it)

---

## Future Research

Beyond Phase 4, potential research directions include:

- **Multi-market market making**: Quote 20+ markets simultaneously
- **Volatility-aware quoting**: Tighten spreads in low-vol, widen in high-vol
- **Cross-market correlation arb**: Exploit correlated events mispricing
- **ML probability models**: Neural network for event probability estimation
- **Hyperliquid execution integration**: Full Rust-based HL order placement
- **Automated parameter tuning**: Bayesian optimization of spread_bps, etc.

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 0.1.0 | 2026-03-09 | Initial scaffold (Phase 0) |
