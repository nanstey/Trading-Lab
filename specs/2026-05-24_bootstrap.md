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

## Phase 5: Agentic Layer

### Objective
Expose a clean, model-agnostic agentic interface: CLI tools the codebase implements + runbooks an external agent runtime (Claude Code, Cursor, a future LLM, or a human operator) can follow. No `anthropic` SDK dependency in the codebase; no model lock-in.

**Prerequisite:** Phase 2 (backtesting) must be fully working before designing this. The agent's primary tool is `make backtest`.

**Design principle:** the codebase provides composable tools and decision playbooks. The agent runtime is external and pluggable.

---

### Step 5.1 — Define Agentic Tool Surface

**[AGENT]** Create CLI scripts that wrap core operations as one-shot, stateless tools. Each takes args, emits structured JSON to stdout, returns predictable exit codes, and never prompts interactively.

Required tools:
- `scripts/eval_strategy.py --strategy <name> --params <json> --markets <ids> --start <date> --end <date>` → JSON: `{sharpe, pnl, max_dd, fill_rate, trades, kill_switch_triggered}`
- `scripts/list_markets.py [--active]` → JSON list of available `(condition_id, yes_token, no_token)` tuples from DataCatalog
- `scripts/promote_config.py --strategy <name> --params <json>` → writes config to `.env`, exits 0 on success
- `scripts/get_live_pnl.py [--window-hours N]` → JSON: `{realized_pnl, unrealized_pnl, fills, kill_switch_state}` from recent logs
- `scripts/halt_trading.py --reason <text>` → triggers `KillSwitch` via filesystem flag, exits 0

**Verification (5.1):**
```bash
python scripts/eval_strategy.py --strategy arb --params '{"min_profit_usdc": 0.02}' --markets <YES,NO> --start 2024-11-01 --end 2024-12-01
# Should output a JSON object — no prose, no progress bars
```

---

### Step 5.2 — Implement Strategy Evaluator

**[AGENT]** Create `src/nautilus_predict/agent/evaluator.py`:

```python
class StrategyEvaluator:
    """Runs backtest grid search and ranks strategy configs by Sharpe ratio."""

    def run_grid(
        self,
        strategy_class,
        param_grid: dict[str, list],
        token_pairs: list[tuple[str, str]],
        start: datetime,
        end: datetime,
    ) -> list[dict]:  # sorted by sharpe descending
        ...
```

This is pure Python — no LLM, no agent. It's the foundation any agent loop builds on, and is independently useful for hand-driven parameter sweeps.

`scripts/eval_strategy.py` (from Step 5.1) is the thin CLI wrapper over this class.

---

### Step 5.3 — Author Agent Runbooks

**[AGENT]** Create markdown runbooks in `runbooks/`. Each runbook is self-contained and can be handed to any agent runtime as the task description.

Required structure per runbook:
- **Task**: what the agent is being asked to do
- **Available tools**: which `scripts/*.py` commands to use, with example invocations
- **Decision rules**: explicit thresholds and branching logic
- **Success criteria**: how the agent knows it's done
- **Escalation**: when to halt and report to a human

Initial runbooks to create:

- `runbooks/strategy-evaluator.md` — "Run a parameter sweep for strategy X over date range Y. Promote the best config to paper if Sharpe > 1.0 AND max DD < 20%. Otherwise report ranked results without promoting."
- `runbooks/live-anomaly-watcher.md` — "Check live PnL every N minutes. If realized loss > $X or fill rate < Y%, call halt_trading.py and escalate."
- `runbooks/new-market-onboarding.md` — "Given a new market token_id, run a 30-day backtest with default params. Report viability."

**Verification (5.3):**
- Each runbook is self-contained — an agent with no prior context can execute it
- Reading any runbook tells you exactly which CLI tools it depends on

---

### Step 5.4 — Author Claude Code Skills (optional)

**[AGENT]** Create `.claude/skills/` entries that wrap runbooks for tighter Claude Code integration. Each skill points at a runbook and provides a slash-command surface:

- `.claude/skills/evaluate-strategy.md` — invokes the strategy-evaluator runbook from `/evaluate-strategy <args>`
- `.claude/skills/check-live-health.md` — quick health check via `get_live_pnl.py`
- `.claude/skills/promote-best-config.md` — runs evaluator and prompts before promoting

Skills are convenience, not architecture. The runbooks + CLI tools remain the canonical interface and work with any agent runtime. Skip this step if you're not using Claude Code as the primary runtime.

---

### Step 5.5 — Validate End-to-End

**[YOU]** Point an external agent (Claude Code, manual operator, or any LLM-driven runtime) at `runbooks/strategy-evaluator.md` with a sample task. Verify:
- The agent reads the runbook and discovers the right tools
- It runs the evaluator over a sensible parameter grid
- It applies the promotion decision rule correctly (promotes only if Sharpe > 1.0)
- It produces a final report a human can review

**Verification (5.5):**
```bash
# Example Claude Code invocation:
claude "Follow runbooks/strategy-evaluator.md to optimize arb strategy params on Nov 2024 data"
# OR if .claude/skills/evaluate-strategy.md exists:
claude /evaluate-strategy --strategy arb --start 2024-11-01 --end 2024-12-01
```

---

## Ongoing: Monitoring & Alerting

**[AGENT]** Add structured metric emission to `strategies/arb_complement.py`:
- Emit JSON log line on every arb scan: `{"event": "arb_scan", "opportunities_found": N, "executed": M}`
- Emit on every fill: `{"event": "fill", "pnl": X, "cumulative_pnl": Y}`

**[YOU]** Set up log-tailing dashboard of choice (tail -f, Grafana + Loki, or Datadog). The structured log output is the monitoring surface.

---

## Summary of Agent vs. User Responsibilities

| Phase | Agent Does | You Do |
|-------|-----------|--------|
| 0 | Fix node.py, config, remove dead code | Run `make test`, confirm clean |
| 0.5 | Update Makefile for uv, add `.gitignore` entry | Install uv, run `make dev`, confirm `make check-env` passes |
| 1 | Implement historical fetch, wire WS ingestion | Discover API endpoints, provide token IDs, run download |
| 2 | Build Parquet adapter, wire BacktestEngine | Interpret Sharpe results, approve progression |
| 3 | Complete WS handlers, wire PaperRunner + TradingNode | Provide credentials, confirm WS message format, run 24h |
| 4 | Wire LiveRunner, graceful shutdown | Complete pre-live checklist, fund account, approve go-live |
| 5 | Build CLI tools, evaluator, runbooks, skills | Validate by pointing an external agent at the runbook |
