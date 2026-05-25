# Implementation Plan: Nautilus-Predict — Phase 0 Completion through Agentic Layer

## Context

The codebase is a well-architected Phase 0 foundation for a Nautilus Trader-based algorithmic trading system targeting Polymarket and Hyperliquid. The risk module, configuration system, authentication, and data catalog are production-ready. However, the system cannot run end-to-end: NautilusTrader's engine is never instantiated, venue client endpoints are partially stubbed, and there are config attribute mismatches in `node.py` that will crash at runtime. The agentic layer (self-managing strategy dev/eval/deploy) does not yet exist.

This plan follows the roadmap's phase-gate discipline: each phase must be verified before the next begins.

**Key architectural decisions baked into this plan:**
- Keep `venues/` (aiohttp, NautilusTrader-integrated) — delete `adapters/` (httpx, standalone)
- Keep `arb_complement.py` / `BinaryArbStrategy` — delete `complement_arb.py` (inconsistent constructor signature)
- `BinaryArbStrategy` is the canonical complement arb implementation going forward

---

## Legend

- **[AGENT]** — steps the agent can complete autonomously (code changes)
- **[YOU]** — steps requiring your action (credentials, external systems, judgement calls)

---

## Deployment Posture

**Current stance: local-first.** All workloads run on the developer machine (laptop / workstation). Hosting is deferred until a specific bottleneck makes it unavoidable.

### What runs locally
- Backtests (`make backtest`) — laptop CPU is fine for grid sizes up to a few hundred runs
- Paper trading (`make paper`) — works as long as the laptop stays awake and connected
- Credential derivation (`scripts/derive_polymarket_keys.py`) — one-shot, local-only
- The agentic layer (Phase 5) when it lands — runbooks invoked locally by Claude Code

### When to revisit (bottleneck triggers)
Reach for a hosted deployment when ANY of the following become true:
1. **Continuous 24h+ paper or live runs** required, and laptop sleep/network drops are interrupting them
2. **Live trading** with non-trivial capital where downtime costs more than ~$30/mo of hosting
3. **Concurrent workloads** — backtest grid jobs + live trader competing for laptop CPU/RAM
4. **Agentic layer needs to be always-on** (e.g., live-anomaly-watcher runbook running on a schedule)

### When that day comes
The existing `Dockerfile` and `docker-compose.yml` are deployment-ready. Reasonable options in rough order of cost:
- **Hetzner CX22** in Frankfurt — ~$5/mo, full control, EU geo dodges any US Polymarket access friction
- **Fly.io** `shared-cpu-2x` + 10GB volume — ~$15-20/mo, easiest deploy from existing Dockerfile
- **Railway** — ~$10-25/mo, GitHub auto-deploy, persistent volumes
- **AWS EC2 t3.medium** — ~$30/mo, closest to Polymarket infra (us-east)

Pick at the moment of need, not in advance. Decisions deferred:
- Provider
- Region (depends on Polymarket access from chosen region)
- Whether to colocate backtest jobs with the live trader or split

### What this changes downstream
- **Phase 3.4** (24h paper run): execute on laptop, plan around your sleep schedule. If interruptions block the gate, that's the bottleneck signal to deploy.
- **Phase 4.2** (pre-live checklist): runs locally. Hosting is a separate decision made later, not a pre-live gate.

---

## Progress

### Phase 0 — Fix the Foundation
- [x] Step 0.1 — Fix `node.py` config mismatches
- [x] Step 0.2 — Remove dead code
- [x] Step 0.3 — Fix runner strategy instantiation
- [x] Step 0.4 — Create AGENTS.md
- [x] Step 0.5 — `make test` passes (66/66) + `make lint` clean

### Phase 0.5 — Python Environment Setup
- [x] Step 0.5a — Install `python3.12-venv`, run `make dev` to bootstrap `.venv`
- [x] Step 0.5b — Update Makefile (`PYTHON := .venv/bin/python3`, `venv` target, `.gitignore`)
- [x] Step 0.5c — `make check-env` 23/23 (switched connectivity check to `data-api.polymarket.com`)

### Phase 1 — Data Infrastructure
- [x] Step 1.1 — Discover correct Polymarket API endpoints
- [ ] Step 1.2 — Implement historical trade fetching
- [ ] Step 1.3 — Implement continuous WebSocket ingestion
- [ ] Step 1.4 — Data validation

### Phase 2 — Backtesting
- [ ] Step 2.1 — Build Parquet → NautilusTrader adapter
- [ ] Step 2.2 — Wire `BacktestRunner` to `BacktestEngine`
- [ ] Step 2.3 — Calibrate and interpret results **[YOU]**

### Phase 3 — Paper Trading
- [ ] Step 3.1 — Complete execution client WebSocket handlers
- [ ] Step 3.2 — Complete data client `TradeTick` handler
- [ ] Step 3.3 — Wire `PaperRunner` to `TradingNode`
- [ ] Step 3.4 — 24-hour paper run **[YOU]**

### Phase 4 — Live Trading
- [ ] Step 4.1 — Wire `LiveRunner` to `TradingNode`
- [ ] Step 4.2 — Pre-live checklist + go-live **[YOU]**

### Phase 5 — Agentic Layer
- [ ] Step 5.1 — Define agentic tool surface (CLI scripts)
- [ ] Step 5.2 — Implement strategy evaluator
- [ ] Step 5.3 — Author agent runbooks
- [ ] Step 5.4 — Author Claude Code skills (optional)
- [ ] Step 5.5 — Validate end-to-end with an external agent **[YOU]**

---

## Phase 0 Completion: Fix the Foundation

### Objective
Close the gap between "declared complete" and "actually runnable." No new features — only fixes to make `make test` and `python -m nautilus_predict` work without crashes.

---

### Step 0.1 — Fix `node.py` Config Mismatches

**[AGENT]** Fix all attribute mismatches in `src/nautilus_predict/node.py`:

| Wrong reference | Correct attribute | Source |
|----------------|-------------------|--------|
| `cfg.polymarket.http_url` | `cfg.polymarket.host` | `config.py:PolymarketConfig` |
| `cfg.polymarket.ws_url` | `cfg.polymarket.ws_host` | `config.py:PolymarketConfig` |
| `cfg.hyperliquid.http_url` | `cfg.hyperliquid.api_url` | `config.py:HyperliquidConfig` |

**[AGENT]** Add missing fields to `config.py`:
- Add `exchange_address: str = ""` to `PolymarketConfig` — this is the Polymarket CLOB Exchange contract address (a constant, not a secret; defaults to `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` for mainnet)
- Add `account_address: str = ""` to `HyperliquidConfig` — the EVM wallet address derived from private key

**[AGENT]** Add strategy config classes to `config.py` (required by `node.py`):

```python
class MarketMakerConfig(BaseSettings):
    spread_bps: float = Field(default=20.0, ge=1.0)
    order_size_usdc: float = Field(default=10.0, ge=1.0)
    max_position_usdc: float = Field(default=100.0, ge=1.0)

class ArbConfig(BaseSettings):
    min_profit_usdc: float = Field(default=0.02, ge=0.0)
    max_capital_usdc: float = Field(default=500.0, ge=1.0)
```

Add these as nested fields on `TradingConfig`:
```python
market_maker: MarketMakerConfig = Field(default_factory=MarketMakerConfig)
arb: ArbConfig = Field(default_factory=ArbConfig)
```

Add corresponding entries to `.env.example`.

**Verification (0.1):**
```bash
python -c "from nautilus_predict.config import load_config; cfg = load_config(); print(cfg.polymarket.host, cfg.arb.min_profit_usdc)"
python -c "from nautilus_predict.node import build_node; print('node.py imports cleanly')"
make lint
```

---

### Step 0.2 — Remove Dead Code (Consolidation)

**[AGENT]** Delete `src/nautilus_predict/adapters/` entirely. The `venues/` tree is the canonical NautilusTrader-integrated implementation.

**[AGENT]** Delete `src/nautilus_predict/strategies/complement_arb.py`. `arb_complement.py` / `BinaryArbStrategy` is the canonical complement arb implementation (NautilusTrader-native, no constructor mismatch).

**[AGENT]** Delete `src/nautilus_predict/venues/polymarket/orders.py` if its signing logic is fully covered by `venues/polymarket/auth.py`. Check for any unique logic first before deleting.

**[AGENT]** Update all `__init__.py` files and imports to remove references to deleted modules.

**Verification (0.2):**
```bash
make test       # all tests pass
make lint       # no import errors
python -c "from nautilus_predict.strategies.arb_complement import BinaryArbStrategy; print('OK')"
```

---

### Step 0.3 — Fix Runner Strategy Instantiation

`runner/paper.py` and `runner/live.py` both instantiate strategies as:
```python
strategy_class(config=self._config, kill_switch=kill_switch)
```

But `BinaryArbStrategy.__init__` takes a `BinaryArbConfig` (a `StrategyConfig`), not a `TradingConfig`. The runner needs to build the right strategy config from `TradingConfig`.

**[AGENT]** Update `runner/paper.py` and `runner/live.py`:
- Build a `BinaryArbConfig` from `self._config.arb` before instantiating the strategy
- Pass `kill_switch` separately (add it as a parameter on `NautilusPredictStrategy.on_start()` or store it on the strategy via a setter after construction — whichever pattern `strategies/base.py` already uses)
- Check `strategies/base.py` and follow existing kill_switch wiring pattern

**Verification (0.3):**
```bash
python -c "
from nautilus_predict.config import load_config, TradingMode
from nautilus_predict.strategies.arb_complement import BinaryArbStrategy, BinaryArbConfig
cfg = load_config()
s_cfg = BinaryArbConfig(min_profit_usdc=cfg.arb.min_profit_usdc, max_capital_usdc=cfg.arb.max_capital_usdc)
s = BinaryArbStrategy(config=s_cfg)
print('Strategy instantiates:', s)
"
```

---

### Step 0.4 — Create AGENTS.md

**[AGENT]** Create `AGENTS.md` at the repo root. This file documents the codebase for any AI agent that works on this project — similar to CLAUDE.md but focused on how an agent should reason about and navigate the system.

The AGENTS.md must cover:

**Project identity and purpose** — what this system is, what markets it targets, what the primary strategy is (complement arb on Polymarket binary markets).

**Canonical module map** — one paragraph per major package:
- `src/nautilus_predict/config.py` — single source of truth for all configuration; always load via `load_config()`
- `src/nautilus_predict/risk/` — do not bypass; KillSwitch, HeartbeatWatcher, PositionLimits are always active in paper/live
- `src/nautilus_predict/venues/polymarket/` — the canonical Polymarket integration; `adapters/` does not exist
- `src/nautilus_predict/strategies/arb_complement.py` — the canonical complement arb strategy (`BinaryArbStrategy`); no other arb strategy file exists
- `src/nautilus_predict/data/` — `catalog.py` for storage, `ingestion.py` for fetch/stream, `parquet_loader.py` for NautilusTrader adapter
- `src/nautilus_predict/runner/` — `backtest.py`, `paper.py`, `live.py`; entry via `make backtest/paper/live`

**What agents must never do:**
- Touch `.env` directly (user-owned); suggest changes via comments only
- Bypass kill switch or remove safety checks in runners
- Add new strategy files without removing the old one first (one canonical implementation per strategy)
- Commit credentials or secrets
- Start live trading or submit real orders (live mode requires `LIVE_TRADING_CONFIRMED=true` which only the user sets)

**Phase gate state** — which phases are complete; what the current blocker is. Agents should update this section when a phase is verified complete.

**How to run tests and verify changes:**
```bash
make test       # unit tests
make lint       # ruff + mypy
make backtest   # end-to-end backtest (requires Phase 1 data)
make paper      # paper trading (requires credentials)
```

**Where to look for things:**
- Strategy configs: `config.py:ArbConfig`, `config.py:MarketMakerConfig`
- NautilusTrader strategy base: `strategies/base.py:NautilusPredictStrategy`
- Risk limits: `config.py:RiskConfig`, enforced in `risk/kill_switch.py` and `risk/position_limits.py`
- Auth: `venues/polymarket/auth.py` (EIP-712 + HMAC-SHA256)
- Instrument creation: `data/parquet_loader.py:make_instrument_id`

**Verification (0.4):**
- AGENTS.md exists at repo root and is committed
- It accurately reflects the current canonical module layout (no references to deleted files)

---

### Step 0.5 — Confirm Tests Pass

**[YOU]** Run the full test suite and confirm clean:
```bash
make test
make lint
```

Expected: all tests pass, no import errors, no type errors.

---

## Phase 0.5: Python Environment Setup (uv)

### Objective
Replace bare `python3`/`pip` with a managed virtual environment using `uv`. All subsequent `make` targets run inside `.venv`, not the system Python. This unblocks local development and `make check-env` passing all package checks.

---

### Step 0.5a — Install uv

**[YOU]** Install `uv` on the host machine:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Restart your shell or: source $HOME/.local/bin/env
uv --version   # should print uv 0.x.x
```

---

### Step 0.5b — Update Makefile and .gitignore

**[AGENT]** Update `Makefile`:
- Change `PYTHON := python3` → `PYTHON := .venv/bin/python3`
- Change `PIP    := $(PYTHON) -m pip` → `PIP    := uv pip`
- Add a `venv` target that creates `.venv` if it doesn't exist:
  ```makefile
  .PHONY: venv
  venv: ## Create .venv using uv (skips if already exists)
      @test -d .venv || uv venv --python 3.12
  ```
- Add `venv` as a prerequisite to `dev` and `install` targets
- Update `dev` message to reflect uv workflow

**[AGENT]** Add `.venv/` to `.gitignore` if not already present.

---

### Step 0.5c — Bootstrap the Environment

**[YOU]** Create the venv and install all dependencies:
```bash
make dev
```

This runs `uv venv --python 3.12` then `uv pip install -e ".[dev]"`. First install of `nautilus_trader` will take a few minutes (large C extension); subsequent runs are cached.

---

### Verification (0.5):
```bash
make check-env
# All package checks should now PASS (was 14/23, target 23/23)
make test
# Tests run inside .venv
```

Expected: `check_env.py` reports all packages installed and API connectivity confirmed.

---

## Phase 1: Data Infrastructure

### Objective
Be able to download historical Polymarket trade data to Parquet and stream live market data continuously.

---

### Step 1.1 — Discover the Correct Historical Data Endpoints

**[YOU]** Manually hit the Polymarket data API to find working endpoints. The existing code references endpoints that may have changed. Run:
```bash
python scripts/check_env.py       # confirm API connectivity
python scripts/fetch_markets.py   # get real token IDs to test with
```

Pick 2–3 active binary markets (YES + NO token pairs) and note their `token_id` values. You'll use these as test subjects throughout Phase 1.

**[YOU]** Using curl or the Python REPL, probe these endpoints with a known `token_id`:
```bash
# Trade history endpoint candidates:
curl "https://data-api.polymarket.com/trades?assetId=<token_id>&limit=100"
curl "https://clob.polymarket.com/trades?assetId=<token_id>&limit=100"

# Orderbook snapshot:
curl "https://clob.polymarket.com/book?token_id=<token_id>"
```

Document which endpoints return valid JSON with trades. Share the confirmed endpoint URLs before proceeding to the next step.

---

### Step 1.2 — Implement Historical Trade Fetching

**[AGENT]** Implement `PolymarketDataIngester.fetch_historical_trades()` in `src/nautilus_predict/data/ingestion.py`:
- Endpoint: `GET https://data-api.polymarket.com/trades?market=<condition_id>&limit=500&offset=N`
- Filter param is `market=<condition_id>` (not `assetId`); each trade record includes an `asset` field (token ID) to distinguish YES vs NO legs
- Paginate using **offset-based** pagination (`offset += limit`) until response is empty or all records are older than `start_ts`; optionally use `before=<unix_ts>` to pre-filter
- Apply rate limiting via `asyncio.Semaphore(5)` (max 5 concurrent requests)
- Write results to `DataCatalog.write_trades()`
- Log progress every 1000 records

**[AGENT]** Update `scripts/download_polymarket_data.py` to call the new method end-to-end.

**Verification (1.2):**
```bash
# Download 30 days for one market (run this yourself with a real token ID)
python scripts/download_polymarket_data.py --token-id <YES_TOKEN_ID> --start 2024-11-01 --end 2024-12-01

# Check catalog
python -c "
from nautilus_predict.data.catalog import DataCatalog
cat = DataCatalog('data')
print(cat.list_available_markets())
print(cat.get_data_summary())
"
```

Expected: Parquet files in `data/parquet/<token_id>/trades/`, non-empty summary.

---

### Step 1.3 — Implement Continuous WebSocket Ingestion

**[AGENT]** The `PolymarketDataIngester.run_continuous()` and `_on_market_message()` methods exist in `data/ingestion.py` but `_on_market_message` needs to handle the actual WS message format coming from `venues/polymarket/client.py:PolymarketWsClient`.

Review the message format that `PolymarketWsClient` delivers via the `on_message` callback (look at `connect_and_run()` in `venues/polymarket/client.py`) and wire `_on_market_message` to correctly parse orderbook and trade events into DataCatalog writes.

**[YOU]** Test live streaming with paper credentials:
```bash
python -c "
import asyncio
from nautilus_predict.config import load_config
from nautilus_predict.data.ingestion import PolymarketDataIngester

async def main():
    cfg = load_config()
    # wire up ingester with real client
    # run for 60 seconds and check catalog

asyncio.run(main())
"
```

---

### Step 1.4 — Data Validation

**[AGENT]** Add a `validate_dataset(token_id, start, end)` method to `DataCatalog` that:
- Checks for time gaps > 5 minutes in trade data
- Reports total record count, date range, and gap count
- Returns a validation report dict

**[YOU]** Run validation on downloaded data:
```bash
python -c "
from nautilus_predict.data.catalog import DataCatalog
from datetime import datetime
cat = DataCatalog('data')
report = cat.validate_dataset('<token_id>', datetime(2024,11,1), datetime(2024,12,1))
print(report)
"
```

Expected: <5% gaps, contiguous coverage of requested range.

**Phase 1 gate check — [YOU]:**
```bash
python scripts/download_polymarket_data.py --token-id <YES_TOKEN_ID> --start 2024-11-01
# Should complete without errors
python -c "from nautilus_predict.data.catalog import DataCatalog; c = DataCatalog('data'); print(c.get_data_summary())"
# Should show > 0 markets and > 0 files
```

---

## Phase 2: Backtesting

### Objective
Run `BinaryArbStrategy` on historical Parquet data end-to-end using NautilusTrader's `BacktestEngine` and get a Sharpe ratio.

---

### Step 2.1 — Build the Parquet → NautilusTrader Adapter

**[AGENT]** Create `src/nautilus_predict/data/parquet_loader.py` with:

```python
def load_trades_as_trade_ticks(
    catalog: DataCatalog,
    token_id: str,
    instrument_id: InstrumentId,
    start: datetime,
    end: datetime,
) -> list[TradeTick]:
    """Reads DataCatalog Parquet and returns NautilusTrader TradeTick list."""
    ...

def make_instrument_id(token_id: str) -> InstrumentId:
    """Creates a NautilusTrader InstrumentId from a Polymarket token_id."""
    ...

def make_instrument(token_id: str, condition_id: str) -> BettingInstrument:
    """Creates a NautilusTrader instrument descriptor for a Polymarket binary token."""
    ...
```

The `BettingInstrument` type from `nautilus_trader.model.instruments` is the correct type for prediction market tokens (probability-priced, 0–1 range). Confirm the type is available in the installed NautilusTrader version before using it; fall back to `Instrument` with custom fields if not.

**Verification (2.1):**
```bash
python -c "
from nautilus_predict.data.parquet_loader import load_trades_as_trade_ticks, make_instrument_id
from nautilus_predict.data.catalog import DataCatalog
from datetime import datetime
cat = DataCatalog('data')
ticks = load_trades_as_trade_ticks(cat, '<token_id>', make_instrument_id('<token_id>'), datetime(2024,11,1), datetime(2024,12,1))
print(f'Loaded {len(ticks)} ticks')
assert len(ticks) > 0
"
```

---

### Step 2.2 — Wire BacktestRunner to BacktestEngine

**[AGENT]** Implement the body of `BacktestRunner.run()` in `src/nautilus_predict/runner/backtest.py`:

1. For each `token_id` in `token_ids`, load TradeTicks via `parquet_loader`
2. Create `BacktestEngineConfig` with `SimulationModuleConfig` for a taker-fee model (2% fee, matching `TAKER_FEE` constant in `arb_complement.py`)
3. Add instruments via `engine.add_instrument()`
4. Add data via `engine.add_data(ticks)`
5. Add venue: `engine.add_venue("POLYMARKET", OmsType.NETTING, ...)`
6. Register `BinaryArbStrategy` via `engine.add_strategy()`
7. Call `engine.run()` then `engine.get_result()`
8. Generate performance report: Sharpe ratio, max drawdown, total PnL, fill rate

**[AGENT]** Add `register_market_pair(condition_id, yes_instrument_id, no_instrument_id)` call before engine.run() — this is how `BinaryArbStrategy` knows which YES/NO tokens to pair. Both `token_ids` passed to the runner should be the YES and NO sides of one condition.

**Verification (2.2):**
```bash
make backtest
# OR:
python scripts/backtest.py --yes-token-id <YES_ID> --no-token-id <NO_ID> --condition-id <COND_ID> --start 2024-11-01 --end 2024-12-01
```

Expected output:
```
Backtest complete
Total trades: N
PnL: $X.XX
Sharpe ratio: X.XX
Max drawdown: $X.XX
Fill rate: XX%
Kill switch triggered: No
```

---

### Step 2.3 — Calibrate and Interpret Results

**[YOU]** Run the backtest on at least 3 different markets with varying liquidity profiles. Review:
- Is the Sharpe ratio positive? (Target: > 1.0)
- Are arb opportunities rare enough that fill rate is realistic?
- Does the kill switch correctly halt the backtest if daily loss limit is hit?

If results are not positive, share the output and we'll adjust `min_profit_usdc` or fee model before proceeding.

**Phase 2 gate check — [YOU]:**
```bash
make backtest    # runs end-to-end without errors
# Strategy produces trades, PnL report is generated
# Kill switch test: set DAILY_LOSS_LIMIT_USDC=-0.01 and verify backtest halts early
```

---

## Phase 3: Paper Trading

### Objective
Connect to live Polymarket feeds, generate paper fills, and run continuously for 24+ hours with kill switch and heartbeat active.

---

### Step 3.1 — Complete Execution Client WebSocket Handlers

**[AGENT]** Implement the stubbed methods in `src/nautilus_predict/venues/polymarket/execution.py`:

**`_connect()`:** Start user-channel WebSocket subscription:
```python
async def _connect(self):
    await self._ws.subscribe_user(markets=[])  # subscribe to all user events
    await self._ws.start()
    self._log.info("Execution client connected")
```

**`_disconnect()`:** Stop WebSocket and close REST session.

**`_handle_order_update(msg)`:** Parse the user-channel order update and emit the correct NautilusTrader event. The message format from Polymarket's user channel looks like:
```json
{"type": "order", "status": "MATCHED", "id": "...", "size_matched": "...", "price": "..."}
```
Map to: `MATCHED` → `OrderFilled`, `CANCELED` → `OrderCanceled`. Use the already-implemented helpers `_send_order_submitted`, `_send_order_accepted`, `_send_order_rejected`, `_send_order_canceled`.

**`_handle_trade_update(msg)`:** Parse trade confirmation and call `_send_order_filled` (implement this helper modeled after the existing `_send_order_*` methods).

**[YOU]** You will need to verify the exact WebSocket message format from Polymarket's user channel. The auth flow is in `venues/polymarket/client.py:PolymarketWsClient._build_ws_auth_token()`. Check the Polymarket CLOB API docs or capture a live WS session to confirm field names before the agent implements the parser.

**Verification (3.1):**
```bash
python -c "
from nautilus_predict.venues.polymarket.execution import PolymarketExecutionClient
# Instantiate with mock deps and call handle_user_ws_message with a sample payload
msg = {'event_type': 'order', 'status': 'MATCHED', 'id': 'test-id', 'size_matched': '10', 'price': '0.52'}
# Verify no exceptions are raised and correct event is emitted
"
```

---

### Step 3.2 — Complete Data Client TradeTick Handler

**[AGENT]** Implement `_handle_trade_event(msg)` in `src/nautilus_predict/venues/polymarket/data.py`:

The `pass` statement needs to become a `TradeTick` construction from the market-channel trade message. Format:
```json
{"event_type": "last_trade_price", "price": "0.52", "size": "...", "timestamp": "..."}
```

Construct: `TradeTick(instrument_id, Price(price, precision), Quantity(size, precision), AggressorSide.NO_AGGRESSOR, TradeId(uuid), ts_event, ts_init)` and publish via `self._handle_data(tick)`.

---

### Step 3.3 — Wire PaperRunner to TradingNode

**[AGENT]** Implement the TODO block in `src/nautilus_predict/runner/paper.py` (lines 83–85):

```python
# Build TradingNode in paper mode
node_config = TradingNodeConfig(
    trader_id="NAUTILUS-PREDICT-PAPER-001",
    log_level=self._config.log_level,
    data_clients={"POLYMARKET": PolymarketDataClientConfig(...)},
    exec_clients={"POLYMARKET": PolymarketExecClientConfig(..., is_paper=True)},
    strategies=[
        ImportableStrategyConfig(
            strategy_path="nautilus_predict.strategies.arb_complement:BinaryArbStrategy",
            config_path="nautilus_predict.strategies.arb_complement:BinaryArbConfig",
            config={"min_profit_usdc": self._config.arb.min_profit_usdc, ...},
        )
    ],
)
node = TradingNode(config=node_config)
```

Wire the `PolymarketLiveDataClientFactory` and `PolymarketLiveExecClientFactory` from `venues/polymarket/factory.py` — these are already implemented and designed exactly for this.

Add kill switch and heartbeat watcher as background tasks alongside `node.run_async()`.

**[YOU]** Set up paper credentials in `.env`:
```
TRADING_MODE=paper
POLYMARKET_API_KEY=<your_key>
POLYMARKET_API_SECRET=<your_secret>
POLYMARKET_API_PASSPHRASE=<your_passphrase>
POLYMARKET_PRIVATE_KEY=<your_l1_key>
```
If you don't have L2 credentials yet, run:
```bash
python scripts/derive_polymarket_keys.py
```

**Verification (3.3):**
```bash
make paper
# Expected log output within 30s:
# INFO  TradingNode connected to POLYMARKET data client
# INFO  Subscribed to order book for token <id>
# INFO  HeartbeatWatcher running, timeout=30s
```

---

### Step 3.4 — 24-Hour Paper Run

_Runs locally — see § Deployment Posture for when to revisit._

**[YOU]** Run paper trading for a continuous 24-hour window. Monitor:
```bash
make paper 2>&1 | tee logs/paper_$(date +%Y%m%d).log
```

During the run, verify:
- [ ] Paper fills are being generated (appears as `INFO OrderFilled` in logs)
- [ ] No crashes or uncaught exceptions over 24h
- [ ] Kill switch test: temporarily set `DAILY_LOSS_LIMIT_USDC=-0.01` and confirm it triggers

**Phase 3 gate check — [YOU]:**
- 24h uptime confirmed in logs
- At least 1 paper fill recorded
- Kill switch confirmed working
- Heartbeat timeout confirmed working (test by blocking network briefly)

---

## Phase 4: Live Trading

### Objective
Deploy with real capital ($100 USDC initial). The system at this point should be functionally identical to paper mode — the difference is real order submission.

---

### Step 4.1 — Wire LiveRunner to TradingNode

**[AGENT]** Implement the TODO block in `src/nautilus_predict/runner/live.py` (lines 132–135):

Same pattern as `PaperRunner` in Step 3.3, but:
- `is_paper=False` on the execution client
- Add `PositionLimits` check before every order (call `limits.check_order()` in the strategy's `_execute_arb` before submitting)
- Add graceful shutdown: on SIGTERM, cancel all open orders before stopping node

**[AGENT]** Implement graceful shutdown in `strategies/arb_complement.py:on_stop()`:
- Cancel any in-flight arb legs (`_abort_arb` for all active arbs)
- Wait for cancellation confirms before returning

---

### Step 4.2 — Pre-Live Checklist

_Runs locally. Hosting is decoupled from the go-live decision — see § Deployment Posture._

**[YOU]** Complete every item before running `make live`:
- [ ] `scripts/check_env.py` runs cleanly with live credentials
- [ ] Polymarket account funded with $100 USDC
- [ ] `.env` has `TRADING_MODE=live` and `LIVE_TRADING_CONFIRMED=true`
- [ ] `MAX_POSITION_USDC=10.0` (conservative initial limit)
- [ ] `DAILY_LOSS_LIMIT_USDC=-50.0`
- [ ] `make paper` has run for 24h without issues
- [ ] `make test` passes on current code

**Verification (4.2 — live):**
```bash
make live
# Within 60s:
# CRITICAL LIVE TRADING ACTIVE — confirm in logs
# INFO  Connected to POLYMARKET execution client
# INFO  First order submitted (small, near-market)
# Verify order appears in Polymarket web UI
```

---

# Plan: Phase 5 Expanded — Autoresearch Loop for Trading Strategies

## Context

You want an agentic system that *systematically* discovers, codes, tests, and graduates trading strategies — with persistent memory so the system stops re-trying ideas that have already been ruled out. The existing [bootstrap spec](../../../Code/Trading-Lab/specs/2026-05-24_bootstrap.md) Phase 5 already plans the foundation (CLI tools, `StrategyEvaluator`, three runbooks). This plan **expands Phase 5 in place** to add: a fully-autonomous discovery loop, agent-written strategy code with hard guardrails, a lifecycle state machine, and a SQLite+Markdown experiment memory. Phases 0–4 must still complete first — agents can't responsibly auto-test strategies until backtest/paper plumbing actually works.

**Your scoping choices (locked in via clarification):**
1. Expand Phase 5 in place — existing 5.1–5.5 fold into the larger plan
2. Fully autonomous discovery crawl (no human watchlist gate)
3. Codegen agent writes `strategies/<slug>.py`, gated by smoke test + lookahead check
4. SQLite for structured state, Markdown for human-readable hypothesis/post-mortem writeups

**Why these choices need extra guardrails:** Autonomous discovery + autonomous codegen is the highest-risk path for an *automated trader*. The two failure modes that quietly kill alpha factories are (a) **lookahead bias** in agent-written code (strategy peeks at future data, posts amazing Sharpe, dies in paper), and (b) **multiple-testing inflation** (test 200 strategies, 10 look "profitable" by chance). The design below treats both as first-class concerns rather than afterthoughts.

---

## Target Architecture (one screen)

```
research/
  hypotheses/<slug>.md         human-readable hypothesis + writeup per strategy
  postmortems/<slug>.md        why a strategy was rejected (linked from hypothesis)
  experiments.db               SQLite — structured truth (lifecycle + results)
  budget.json                  daily token/backtest budget counter
  sources.yaml                 discovery watchlist (arxiv cats, SSRN feeds, blogs)

src/nautilus_predict/
  strategies/<slug>.py         agent-written or human-written strategy code
  agent/
    lifecycle.py               only module that writes to experiments.db
    experiment_log.py          structured backtest result persistence
    discovery.py               source poller + dedup
    codegen_guards.py          lookahead static analysis, import allowlist
    evaluator.py               (existing plan) grid + walk-forward
    budget.py                  LLM/backtest budget tracker

scripts/                       agentic CLI surface (JSON in, JSON out, exit codes)
  research_cli.py              query experiments.db; one entry point for all lifecycle reads
  propose_hypothesis.py        write a hypothesis MD + DB row, status=PROPOSED
  generate_strategy.py         (calls external agent runtime) → strategies/<slug>.py
  smoke_test_strategy.py       synthetic-data smoke + lookahead AST check
  transition_lifecycle.py      move a strategy between states atomically
  eval_strategy.py             (existing plan) grid eval, writes to experiments.db
  promote_config.py            (existing plan) writes .env, dry-run default

runbooks/                      task descriptions for any agent runtime
  discover-strategies.md       crawl sources.yaml, dedup, queue new hypotheses
  codegen-strategy.md          drain PROPOSED queue, write code, smoke-test
  test-strategy.md             drain BACKTEST queue, run eval, apply decision rules
  optimize-strategy.md         drain OPTIMIZE queue, walk-forward, pick winner
  live-anomaly-watcher.md      (existing plan) monitor live PnL
  strategy-evaluator.md        (existing plan) hand-driven sweep
```

---

## Lifecycle State Machine

States stored in SQLite. All transitions go through `agent/lifecycle.py` → atomic transaction.

```
PROPOSED  ──codegen──▶  CODEGEN  ──smoke──▶  SMOKE_PASS  ──backtest──▶  BACKTEST
                          │                                                  │
                          ▼ smoke_fail                                       ▼
                       REJECTED                                  ┌──Sharpe<0──REJECTED
                                                                 ├──marginal──SHELVED
                                                                 └──Sharpe≥1──OPTIMIZE
                                                                                │
                                                                                ▼
                                                            WALK_FORWARD ◀──sweep
                                                                  │
                                                ┌─OOS Sharpe<0.7──REJECTED
                                                ├─OOS Sharpe<1.0──SHELVED
                                                └─OOS Sharpe≥1.0──PAPER_READY
                                                                  │
                                            [YOU approve] ────────┘
                                                  │
                                                  ▼
                                                PAPER  ──24h clean──▶  LIVE_READY
                                                                          │
                                                            [YOU only] ───┘
                                                                  │
                                                                  ▼
                                                                LIVE  ──drawdown──▶  RETIRED
```

**Hard rule: no agent may transition into `PAPER_READY → PAPER` or `LIVE_READY → LIVE`.** Those gates are `[YOU]`. Phase 4's `LIVE_TRADING_CONFIRMED=true` rule extends here: agents can drive everything up to `PAPER_READY` and `LIVE_READY` but a human flips the switch.

---

## Expanded Phase 5 Steps

### 5.1 — Agentic CLI tool surface
**[AGENT]** Build the scripts listed above. Each one: argparse → does its thing → `print(json.dumps(...))` → exits 0/non-zero. No interactive prompts, no progress bars. Existing planned tools (`eval_strategy.py`, `list_markets.py`, `promote_config.py`, `get_live_pnl.py`, `halt_trading.py`) remain.

**Verification:** `python scripts/research_cli.py list --state BACKTEST` returns valid JSON.

---

### 5.2 — Experiment DB schema + lifecycle module
**[AGENT]** Create [research/experiments.db](../../../Code/Trading-Lab/research/) (SQLite) with:

```sql
CREATE TABLE hypotheses (
  slug TEXT PRIMARY KEY,
  source_url TEXT, source_type TEXT,           -- 'arxiv'|'ssrn'|'blog'|'manual'
  summary TEXT, summary_embedding BLOB,         -- for dedup
  state TEXT NOT NULL,                          -- enum from state machine
  rejection_reason TEXT, rejection_category TEXT,
  parent_slug TEXT,                             -- for derivative strategies
  created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE TABLE experiments (
  id INTEGER PRIMARY KEY, slug TEXT, params_json TEXT,
  data_start TIMESTAMP, data_end TIMESTAMP,
  sharpe REAL, max_dd REAL, fill_rate REAL, pnl REAL, n_trades INTEGER,
  walk_forward_oos_sharpe REAL,                 -- NULL until WF run
  code_hash TEXT, data_hash TEXT,               -- reproducibility
  kill_switch_triggered BOOLEAN, created_at TIMESTAMP,
  FOREIGN KEY(slug) REFERENCES hypotheses(slug)
);
CREATE TABLE lifecycle_transitions (
  id INTEGER PRIMARY KEY, slug TEXT,
  from_state TEXT, to_state TEXT, reason TEXT,
  actor TEXT,                                   -- 'agent:codegen'|'agent:tester'|'user'
  timestamp TIMESTAMP, FOREIGN KEY(slug) REFERENCES hypotheses(slug)
);
CREATE TABLE budget_ledger (                    -- daily LLM token + backtest counters
  date TEXT PRIMARY KEY, llm_tokens INTEGER, backtests INTEGER,
  paper_starts INTEGER, live_starts INTEGER
);
```

`agent/lifecycle.py` is the **only** module allowed to `INSERT INTO lifecycle_transitions` or `UPDATE hypotheses SET state=...`. Every transition logs `from_state, to_state, reason, actor`.

**Verification:** `python scripts/transition_lifecycle.py --slug test --to SMOKE_PASS --reason "manual"` then `research_cli.py history --slug test` shows the transition.

---

### 5.3 — Discovery loop
**[AGENT]** Build `agent/discovery.py`. The runbook `runbooks/discover-strategies.md` is the agent-facing entry point.

**Sources** (default `sources.yaml`):
- arxiv `q-fin.TR`, `q-fin.PM`, `q-fin.ST` — last 7 days
- Quantocracy RSS
- Papers-with-code "trading" / "market-microstructure" tags
- Configurable blog list (start: Hudson & Thames, ML for Trading, Robot Wealth)
- A `manual_inbox/` directory you can drop URLs into

**Dedup strategy (layered):**
1. **Exact:** SHA256 of source URL — skip if already in hypotheses table
2. **Semantic:** embed the extracted summary with `sentence-transformers/all-MiniLM-L6-v2` (small, local, no API). Cosine similarity > 0.85 against existing hypotheses → mark as `parent_slug` derivative rather than new
3. **Negative-results check:** before queueing, look up `rejection_category` field across past rejections. If new hypothesis matches a previously-rejected category (e.g., "momentum on PM binaries"), the new hypothesis MD includes a `Prior attempts` section listing past failures. The discovery agent then **drops** the hypothesis unless its summary explicitly addresses the prior failure mode.

**Rate limit:** Max 5 new hypotheses queued per day (configurable). Prevents queue floods.

**Output per hypothesis:**
- `research/hypotheses/<slug>.md` with frontmatter: `source`, `created`, `parent_slug`, `prior_attempts`, body has Hypothesis / Edge Claimed / Required Data / Parameter Space / Acceptance Criteria
- SQLite row, `state=PROPOSED`

**Verification:** Run discovery on a seeded sources.yaml with one arxiv paper. Confirm hypothesis MD + DB row created; running it again creates nothing.

---

### 5.4 — Codegen + smoke loop (the risky part)
**[AGENT]** `runbooks/codegen-strategy.md`: drain `PROPOSED` → produce `strategies/<slug>.py` + `tests/strategies/test_<slug>.py`. Transition `PROPOSED → CODEGEN → SMOKE_PASS|REJECTED`.

**Mandatory guardrails enforced by `scripts/smoke_test_strategy.py`:**
1. **Import allowlist (AST scan):** strategy file may only import from `nautilus_trader.*`, `nautilus_predict.*`, `numpy`, `pandas`, stdlib. No `requests`, `urllib`, `subprocess`, `os.system`, no relative imports of weird stuff. Blocks data leakage and code escape.
2. **Lookahead static check (AST):** `on_book_update(self, snapshot)` and similar handlers may only reference `self`, their args, and module-level constants. Reject if the function reads from any module-level mutable that's populated by a later timestamp (heuristic: any attribute named `*_future*`, `*_next*`, or that's modified inside `on_*` callbacks and read by earlier ones in event-time order).
3. **Synthetic smoke test:** generate 1 hour of synthetic ticks (random walk around 0.5 for a binary token), instantiate strategy with default config, feed ticks, assert: completes without exception, emits ≥0 orders, no order has `ts > current_tick_ts`.
4. **Required test file:** `tests/strategies/test_<slug>.py` must exist and pass under `pytest`.
5. **Code hash recorded:** `code_hash = sha256(strategy.py)` written to the next experiment row so any future result is tied to the exact code.

Failure → `REJECTED` with `rejection_category` in `{import_violation, lookahead_suspected, smoke_crash, test_missing, test_fail}`. Post-mortem MD auto-generated with the specific AST node / exception that failed.

**Verification:** Hand-craft a deliberately-lookahead-biased strategy file and confirm the smoke script catches it.

---

### 5.5 — Testing loop
**[AGENT]** `runbooks/test-strategy.md`: drain `SMOKE_PASS` → run `eval_strategy.py` over the hypothesis's declared parameter grid → write experiments rows → transition based on decision rules:

| Sharpe (in-sample) | Max DD | Action | New state |
|---|---|---|---|
| < 0 | any | reject | REJECTED (`unprofitable`) |
| 0 ≤ S < 0.5 | any | shelf | SHELVED (`marginal_is`) |
| 0.5 ≤ S < 1.0 | > 25% | reject | REJECTED (`high_dd`) |
| 0.5 ≤ S < 1.0 | ≤ 25% | shelf | SHELVED (`marginal_is`) |
| ≥ 1.0 | ≤ 20% | promote | OPTIMIZE |

**Multiple-testing correction:** the Sharpe threshold above scales by the number of distinct hypotheses tested in the last 30 days using Bonferroni on a baseline of α=0.05. `agent/evaluator.py:adjusted_sharpe_threshold(n_tests)` returns the corrected cutoff. The decision table uses the corrected number, not the raw 1.0.

---

### 5.6 — Optimize + walk-forward
**[AGENT]** `runbooks/optimize-strategy.md`: drain `OPTIMIZE`. Fine-grained parameter sweep. Pick winner by **out-of-sample walk-forward Sharpe**, never in-sample. Default split: 70% train / 30% test, rolled across 3 non-overlapping windows.

Transition rules (using OOS Sharpe):
- OOS Sharpe ≥ 1.0 and OOS Sharpe ≥ 0.6 × IS Sharpe → `PAPER_READY`
- OOS Sharpe ≥ 0.7 but < 1.0 → `SHELVED` (`marginal_oos`)
- OOS Sharpe < 0.7 → `REJECTED` (`overfit`) ← the most important rejection category; explicitly catches the "looked great in-sample, dies out-of-sample" failure

---

### 5.7 — Paper / live promotion (HUMAN GATE)
**[YOU]** `research_cli.py review --state PAPER_READY` shows a digest: hypothesis summary, best params, IS/OOS Sharpe, walk-forward stability plot. You decide to promote. Same gate at `LIVE_READY → LIVE` (re-uses Phase 4's `LIVE_TRADING_CONFIRMED=true` rule).

No agent may write to `.env` for paper/live promotion. `promote_config.py` defaults to `--dry-run`; the `--apply` flag is wrapped in a runbook that says "only invoke if a human just said yes in this session."

---

### 5.8 — Negative-results memory (the "don't try again" requirement)
This is implicit in the lifecycle but worth stating: every `REJECTED` transition produces:
1. `research/postmortems/<slug>.md` — what was tried, what failed, which guardrail / threshold caught it
2. SQLite row with `rejection_category` (one of ~10 enum values)
3. The discovery loop (5.3) consults this on every new hypothesis to (a) annotate similar new ideas with `prior_attempts`, (b) outright drop if rejection_category matches and the new hypothesis doesn't explicitly address the failure mode

This is what stops the system from spinning forever on "momentum on prediction markets" once you've shown it doesn't work.

---

### 5.9 — Budget + concurrency
**[AGENT]** `agent/budget.py` tracks LLM tokens spent and backtests run per day. Each runbook checks budget before starting work. Hard caps default to: 100k tokens/day, 50 backtests/day, 1 paper promotion/week, 0 live promotions (human-only).

**Concurrency:** Agents acquire a sqlite-level `BEGIN IMMEDIATE` lock when transitioning a hypothesis's state. Two testing loops cannot grab the same slug. No external lock files needed.

---

### 5.10 — Continuous operation (optional)
**[AGENT]** Cron entries (or `loop` skill invocations) for scheduled drainage:
- Discovery: `0 9 * * *` (daily 09:00)
- Codegen: `0 */2 * * *` (every 2h)
- Testing: `0 */6 * * *` (every 6h)
- Optimize: `0 3 * * *` (daily 03:00)
- All check budget first and exit if exhausted.

---

### 5.11 — End-to-end validation
**[YOU]** Seed the system with a known-bad hypothesis ("trade the daily open/close gap on PM binaries"). Run discovery → codegen → smoke → backtest → expect `REJECTED` with category `unprofitable`. Verify post-mortem MD written, SQLite row correct. Then seed a known-good hypothesis (the existing complement-arb logic, repackaged as a new hypothesis MD) and verify it promotes to `PAPER_READY` cleanly.

---

## Critical files to be created (modify-list)

| Path | Why |
|---|---|
| [src/nautilus_predict/agent/lifecycle.py](../../../Code/Trading-Lab/src/nautilus_predict/agent/) | Only writer to experiments.db state |
| [src/nautilus_predict/agent/experiment_log.py](../../../Code/Trading-Lab/src/nautilus_predict/agent/) | Records backtest results with code+data hashes |
| [src/nautilus_predict/agent/discovery.py](../../../Code/Trading-Lab/src/nautilus_predict/agent/) | Source polling + dedup |
| [src/nautilus_predict/agent/codegen_guards.py](../../../Code/Trading-Lab/src/nautilus_predict/agent/) | AST checks (lookahead, import allowlist) |
| [src/nautilus_predict/agent/budget.py](../../../Code/Trading-Lab/src/nautilus_predict/agent/) | Token + backtest budget |
| [src/nautilus_predict/agent/evaluator.py](../../../Code/Trading-Lab/src/nautilus_predict/agent/) | Already in Phase 5.2 plan — add walk-forward + Bonferroni helpers |
| [scripts/research_cli.py](../../../Code/Trading-Lab/scripts/) | Query/inspect facade over experiments.db |
| [scripts/smoke_test_strategy.py](../../../Code/Trading-Lab/scripts/) | Runs the 5.4 guardrails |
| [scripts/transition_lifecycle.py](../../../Code/Trading-Lab/scripts/) | Single atomic-write entry for state changes |
| [scripts/propose_hypothesis.py](../../../Code/Trading-Lab/scripts/) | CLI to add hypothesis from inbox file |
| [scripts/generate_strategy.py](../../../Code/Trading-Lab/scripts/) | Wraps agent runtime to draft strategy code |
| [runbooks/discover-strategies.md](../../../Code/Trading-Lab/runbooks/) | Discovery loop instructions |
| [runbooks/codegen-strategy.md](../../../Code/Trading-Lab/runbooks/) | Codegen + smoke loop |
| [runbooks/test-strategy.md](../../../Code/Trading-Lab/runbooks/) | Testing loop with decision rules |
| [runbooks/optimize-strategy.md](../../../Code/Trading-Lab/runbooks/) | Walk-forward optimization |
| [research/sources.yaml](../../../Code/Trading-Lab/research/) | Discovery source config |
| [research/experiments.db](../../../Code/Trading-Lab/research/) | SQLite, gitignored |
| Update [AGENTS.md](../../../Code/Trading-Lab/AGENTS.md) | Document the lifecycle, what agents may/may not do |
| Update [Makefile](../../../Code/Trading-Lab/Makefile) | `make research-discover`, `make research-test`, `make research-status` |
| Update [pyproject.toml](../../../Code/Trading-Lab/pyproject.toml) | Add `sentence-transformers` (or defer — see Open Questions) |
| Update [specs/2026-05-24_bootstrap.md](../../../Code/Trading-Lab/specs/2026-05-24_bootstrap.md) | Replace existing Phase 5 with the expanded version |

**Existing functions/utilities to reuse:**
- `DataCatalog` ([src/nautilus_predict/data/catalog.py](../../../Code/Trading-Lab/src/nautilus_predict/data/catalog.py)) for `data_hash` calc (hash of `(token_ids, start, end, get_data_summary())`)
- `KillSwitch` ([src/nautilus_predict/risk/kill_switch.py](../../../Code/Trading-Lab/src/nautilus_predict/risk/kill_switch.py)) — extend to also halt the research loop, not just trading
- `NautilusPredictStrategy` base class ([src/nautilus_predict/strategies/base.py](../../../Code/Trading-Lab/src/nautilus_predict/strategies/base.py)) — every agent-written strategy inherits from this; codegen template should be skeletoned around it
- `BinaryArbConfig` pattern ([src/nautilus_predict/strategies/arb_complement.py](../../../Code/Trading-Lab/src/nautilus_predict/strategies/arb_complement.py)) — every new strategy needs a paired `*Config(StrategyConfig)`; codegen agent must produce both

---

## Open questions I'd flag before implementing

1. **Embedding model for dedup.** `sentence-transformers` adds ~500MB of model weights. Alternatives: skip semantic dedup (URL hash only), or call a hosted embedding API (re-introduces SDK lock-in the spec deliberately avoids).
2. **Where does the codegen agent itself run?** `generate_strategy.py` needs *some* LLM runtime to actually draft Python. Three options: (a) shell out to `claude` CLI, (b) shell out to any model via a thin local wrapper, (c) leave it as a runbook step a human Claude Code session executes. Phase 5 of the existing spec leans (c); fully-autonomous mode wants (a) or (b).
3. **Are you OK with all-strategies-tested-equally?** The discovery agent could prioritize by claimed Sharpe in the source paper, by source credibility, or by required data availability. The plan above is FIFO; smarter prioritization is a v2.
4. **Live drawdown trigger to `RETIRED`.** Threshold value (e.g., -10% from peak) needs a number — left unspecified above.

---

## Verification (end-to-end)

```bash
# 0. Build out
make install                                                # adds new deps
python -c "from nautilus_predict.agent.lifecycle import init_db; init_db()"

# 1. Seed a hypothesis manually
python scripts/propose_hypothesis.py --file specs/test_hypothesis.md
python scripts/research_cli.py list --state PROPOSED        # shows 1 row

# 2. Codegen + smoke
python scripts/generate_strategy.py --slug test-momentum
python scripts/smoke_test_strategy.py --slug test-momentum  # exits 0 or rejects

# 3. Backtest (uses existing eval_strategy.py wired to experiments.db)
python scripts/eval_strategy.py --slug test-momentum --start 2024-11-01 --end 2024-12-01

# 4. Inspect
python scripts/research_cli.py show --slug test-momentum    # current state, last transition, last result
python scripts/research_cli.py history --slug test-momentum # all transitions

# 5. Negative-results check
python scripts/research_cli.py list --state REJECTED --category overfit

# 6. (Optional) Loop test — let it run autonomously for 24h with budget caps and verify nothing escapes the human gate
```

The final acceptance test is the one in 5.11: seed a known-bad and a known-good, watch the system rejection-reason the first and promote the second to `PAPER_READY` without manual intervention.
