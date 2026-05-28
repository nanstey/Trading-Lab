# Implementation Plan: Hyperliquid — Paper Trading + Testnet

## Context

The Hyperliquid scaffolding (`src/trading_lab/venues/hyperliquid/`) is execution-only and mainnet-only:
- `auth.py`, `client.py` (REST + WS), `data.py`, `execution.py`, `factory.py` are wired and could in principle place orders.
- `is_paper=True` on `HyperliquidExecutionClient` short-circuits to `OrderAccepted` and **never emits `OrderFilled`** — strategies in PAPER state would book zero pnl. There is no analogue to `PolymarketPaperFillEngine` for HL.
- `config/venues.yaml` hardcodes mainnet endpoints (`api.hyperliquid.xyz`, `wss://api.hyperliquid.xyz/ws`). There is no path to testnet without editing yaml in place.
- No HL strategies have ever run (paper, testnet, or live). HL is listed as "scaffolded only" in the project memory.

This spec stands up the two missing modes needed before HL strategy work can begin:

1. **Paper** — in-process fill simulation against the live HL book (mirrors the Polymarket pattern). No network writes. No funded account required.
2. **Testnet** — real signing, real network, real fills, against Hyperliquid's testnet. Faucet-funded; no real capital at risk.

Mainnet ("live") wiring already exists and is unchanged by this spec.

**Architectural commitments preserved:**
- Paper-vs-live remains a **per-strategy** concern (hypothesis lifecycle state). This spec does not introduce a `TRADING_MODE` env var.
- Testnet-vs-mainnet is a **per-runner / per-process** choice (venue endpoint selection at launch). It is orthogonal to paper-vs-live.
- The triple-gate (`LIVE_TRADING_CONFIRMED`, hypothesis state=LIVE, `--i-understand-this-is-live`) gates **real-money writes only**. Testnet writes do not require the env or CLI gate — the lifecycle state gate (≥LIVE_READY) still applies.
- Config split: testnet endpoints live in `config/venues.yaml`; secrets stay in `.env`. No new env vars beyond a separate `HL_TESTNET_PRIVATE_KEY` for the testnet API wallet (mainnet and testnet API wallets are distinct accounts and must not share keys).

---

## Legend

- **[AGENT]** — autonomous code changes
- **[YOU]** — credentials, external systems, judgement calls

---

## Objective

Allow a strategy to be run end-to-end against Hyperliquid in two new modes:

```
make paper-hl     HYPOTHESIS=<slug>                    # in-process fill sim, no HL writes
make live-hl      HYPOTHESIS=<slug> --testnet          # real signing, HL testnet endpoints
make live-hl      HYPOTHESIS=<slug> --i-understand-this-is-live   # mainnet (unchanged)
```

with `make check-env` reporting connectivity to whichever endpoint is configured.

---

## Phase H0 — Operator Setup (no code)

### Step H0.1 — Create the testnet API wallet

**[YOU]** Browser flow:

1. Open `app.hyperliquid-testnet.xyz` in the same MetaMask session you used for mainnet. (The testnet app uses the same wallet but a separate account state — testnet positions/balances do not carry over.)
2. Connect MetaMask. The app may prompt to add the testnet network; accept.
3. **Fund**: open the faucet (top bar → Faucet, or `https://app.hyperliquid-testnet.xyz/drip`). Request testnet USDC. Refill at the documented interval if you run dry.
4. Go to **More → API → Generate** to create an API wallet for testnet. Sign the authorization in MetaMask. Copy the private key **at the moment it is shown** — it is not retrievable later. Save it as `HL_TESTNET_PRIVATE_KEY` (Step H0.3).

### Step H0.2 — (Optional) Create the mainnet API wallet

**[YOU]** Repeat the same flow on `app.hyperliquid.xyz` to generate a **separate** mainnet API wallet. Save as `HL_PRIVATE_KEY`. Mainnet and testnet API wallets must not be the same key.

If you are not planning mainnet trading yet, skip this step — the testnet key is enough for the work in this spec.

### Step H0.3 — `.env` slots

**[AGENT]** Extend `.env.example`:

```
# Hyperliquid — mainnet API wallet (REQUIRED for live-hl on mainnet)
HL_PRIVATE_KEY=0x...
HL_ACCOUNT_ADDRESS=

# Hyperliquid — testnet API wallet (REQUIRED for live-hl --testnet)
# Distinct from HL_PRIVATE_KEY. Generated at app.hyperliquid-testnet.xyz.
HL_TESTNET_PRIVATE_KEY=
HL_TESTNET_ACCOUNT_ADDRESS=
```

**[YOU]** Copy values into your local `.env`. Never commit.

**Verification (H0):**
```bash
make check-env
# Expected: HL_TESTNET_PRIVATE_KEY present; HL_PRIVATE_KEY optional.
```

---

## Phase H1 — Testnet profile in `config/venues.yaml`

### Step H1.1 — Add a `testnet` block

**[AGENT]** Restructure the `hyperliquid:` section of `config/venues.yaml`:

```yaml
hyperliquid:
  mainnet:
    api_url: https://api.hyperliquid.xyz
    ws_url: wss://api.hyperliquid.xyz/ws
  testnet:
    api_url: https://api.hyperliquid-testnet.xyz
    ws_url: wss://api.hyperliquid-testnet.xyz/ws
  # Active network for runners; CLI flag --testnet overrides to "testnet".
  default_network: mainnet
```

### Step H1.2 — Update `HyperliquidConfig`

**[AGENT]** In `src/trading_lab/config.py`:

- Replace the flat `HyperliquidConfig(api_url, ws_url, account_address)` with a nested form:

  ```python
  class HyperliquidNetworkConfig(BaseSettings):
      api_url: str
      ws_url: str

  class HyperliquidConfig(BaseSettings):
      mainnet: HyperliquidNetworkConfig
      testnet: HyperliquidNetworkConfig
      default_network: Literal["mainnet", "testnet"] = "mainnet"
      account_address: str = ""           # mainnet — derived from HL_PRIVATE_KEY if blank
      testnet_account_address: str = ""   # testnet — derived from HL_TESTNET_PRIVATE_KEY

      def active(self, network: str | None = None) -> HyperliquidNetworkConfig:
          return getattr(self, network or self.default_network)
  ```

- `load_config()` reads `HL_PRIVATE_KEY` → `mainnet`; `HL_TESTNET_PRIVATE_KEY` → `testnet`.

### Step H1.3 — Update `check_env.py`

**[AGENT]** `scripts/check_env.py`:

- `check_hyperliquid_connectivity` accepts a `network: Literal["mainnet", "testnet"]` arg and pings the URL from `cfg.venues.hyperliquid.active(network).api_url`.
- Report both networks when both sets of credentials are present; otherwise only the configured network.
- Add a `--network` CLI flag (default = `cfg.venues.hyperliquid.default_network`).

**Verification (H1):**
```bash
make check-env-offline                 # config parses, both networks visible
make check-env                         # pings configured network
.venv/bin/python scripts/check_env.py --network testnet
# Expected: "Hyperliquid API (testnet): OK"
```

---

## Phase H2 — Paper fill engine for Hyperliquid

### Step H2.1 — `HyperliquidPaperFillEngine`

**[AGENT]** Create `src/trading_lab/venues/hyperliquid/paper_fill.py`. Mirror `venues/polymarket/paper_fill.py:PolymarketPaperFillEngine` exactly:

- NT `Actor` subscribed to `OrderBookDeltas` for every instrument touched by paper orders.
- Maintains per-instrument best-bid / best-ask from inbound deltas.
- Exposes `register_order(order)` that the execution client calls in `is_paper=True` after `_send_order_accepted`.
- On each book update, sweeps pending orders:
  - **BUY**: fill when `best_ask <= order.price`; fill price = `best_ask`.
  - **SELL**: fill when `best_bid >= order.price`; fill price = `best_bid`.
  - **IOC**: fill on first crossing book update, else emit `OrderCanceled`.
- Full-or-nothing fills (no partials) in v1 — same simplification as the PM engine.
- Emit `OrderFilled` with `LiquiditySide.TAKER` (the strategy crossed the spread to fill against the resting book).

The fee handling differs from PM: HL charges taker fees in USDC denominated against notional. Use the rate from `config/portfolio.yaml:hyperliquid_fees.taker_bps` (add this key with default `4.5` bps = 0.045%, matching current public HL taker tier) and bake it into the fill commission. **No fees** in PAPER would silently inflate backtests-vs-paper consistency.

### Step H2.2 — Wire `HyperliquidExecutionClient` to the engine

**[AGENT]** In `src/trading_lab/venues/hyperliquid/execution.py`:

- Add `paper_fill_engine: HyperliquidPaperFillEngine | None = None` to the constructor.
- In `_submit_order` under `if self._is_paper:`:
  - After `_send_order_accepted`, also `self._paper_fill_engine.register_order(order)`.
- In `_cancel_order` under `if self._is_paper:`:
  - Also call `self._paper_fill_engine.cancel_order(client_order_id)` before emitting `OrderCanceled`.

### Step H2.3 — Factory wiring

**[AGENT]** In `src/trading_lab/venues/hyperliquid/factory.py`:

- `HyperliquidLiveExecClientFactory.create` reads `config["is_paper"]`. If true, instantiate `HyperliquidPaperFillEngine(...)` and pass it to the execution client.
- The engine is also registered as an Actor on the TradingNode (see Phase H3) so it receives `OrderBookDeltas`.

**Verification (H2):**
```bash
.venv/bin/python -c "
from trading_lab.venues.hyperliquid.paper_fill import HyperliquidPaperFillEngine
print(HyperliquidPaperFillEngine)
"
make test                              # existing 112 tests still pass
make test-hl-paper                     # new test file: tests/venues/hyperliquid/test_paper_fill.py
# Expected new tests:
#   - register + book cross emits OrderFilled at touch price
#   - BUY above ask fills; BUY below ask sits
#   - IOC that misses emits OrderCanceled on next book update
#   - taker fee applied to OrderFilled.commission
```

---

## Phase H3 — Runner integration

### Step H3.1 — `paper_run_v2` accepts HL

**[AGENT]** Generalise `src/trading_lab/runner/paper_v2.py` to accept a `venue: Literal["polymarket", "hyperliquid"]` argument (derived from the hypothesis's `venue:` frontmatter, default `polymarket` for backward compat). When `venue="hyperliquid"`:

- Build `TradingNodeConfig` with `data_clients={"HYPERLIQUID": ...}` and `exec_clients={"HYPERLIQUID": {..., "is_paper": True, "http_url": cfg.venues.hyperliquid.active().api_url, "ws_url": ...}}`.
- Register `HyperliquidLiveDataClientFactory` / `HyperliquidLiveExecClientFactory`.
- Add the `HyperliquidPaperFillEngine` Actor to the node so it gets the msgbus subscription.
- Use `HL_PRIVATE_KEY` (or `HL_TESTNET_PRIVATE_KEY` if `--testnet` is passed even in paper — useful because `default_network=testnet` lowers blast radius for paper data subscriptions too; key is used only for `userFills` channel auth, never to write).

### Step H3.2 — `live_run` accepts HL + `--testnet`

**[AGENT]** Generalise `scripts/live_run.py` / `runner/live_v2.py`:

- New CLI flag `--testnet` (mutually exclusive with `--i-understand-this-is-live`).
- Resolution table for HL:

  | Flags | Network | `is_paper` | Real money? | Required gates |
  |---|---|---|---|---|
  | (none) | testnet | False | No | hypothesis state ≥ `LIVE_READY` |
  | `--testnet` | testnet | False | No | hypothesis state ≥ `LIVE_READY` |
  | `--i-understand-this-is-live` | mainnet | False | **Yes** | `LIVE_TRADING_CONFIRMED=true` + state=`LIVE` + CLI flag |

- Refuse to start if `--testnet` and `--i-understand-this-is-live` are both passed.
- Refuse to start mainnet if `HL_PRIVATE_KEY` is missing; refuse to start testnet if `HL_TESTNET_PRIVATE_KEY` is missing.
- Emit a structured `runner_start` event to `logs/events.jsonl` including `{venue: hyperliquid, network: testnet|mainnet, is_paper: bool, hypothesis_slug}`.

### Step H3.3 — Makefile targets

**[AGENT]** Add to `Makefile`:

```makefile
.PHONY: paper-hl
paper-hl: ## Paper-trade an HL hypothesis (in-process fills, no network writes)
	$(PYTHON) scripts/paper_run_v2.py --venue hyperliquid --hypothesis-slug $(HYPOTHESIS)

.PHONY: live-hl-testnet
live-hl-testnet: ## Run HL hypothesis against TESTNET (faucet USDC; no real money)
	$(PYTHON) scripts/live_run.py --venue hyperliquid --testnet --hypothesis-slug $(HYPOTHESIS)

.PHONY: live-hl
live-hl: ## Run HL hypothesis against MAINNET (real money — requires triple-gate)
	$(PYTHON) scripts/live_run.py --venue hyperliquid --hypothesis-slug $(HYPOTHESIS) --i-understand-this-is-live
```

**Verification (H3):**
```bash
# Sanity: refuses mainnet without flag
make live-hl HYPOTHESIS=stub
# Expected exit: "--i-understand-this-is-live required for HL mainnet"

# Sanity: refuses testnet without testnet key
HL_TESTNET_PRIVATE_KEY= make live-hl-testnet HYPOTHESIS=stub
# Expected exit: "HL_TESTNET_PRIVATE_KEY missing"

# Smoke: a stub HL hypothesis can paper-run for 30s with the engine producing fills
make paper-hl HYPOTHESIS=hl-smoke
# Expected: at least one OrderFilled event in logs/events.jsonl
```

---

## Phase H4 — Lifecycle integration

### Step H4.1 — Hypothesis frontmatter accepts `venue`

**[AGENT]** Extend `agent/lifecycle.py` (or wherever hypothesis MD parsing lives) so the frontmatter supports:

```yaml
venue: hyperliquid       # default: polymarket
```

Strategies are validated against the venue at smoke-test time (`scripts/smoke_test_strategy.py` already imports the strategy class; have it check that the strategy's expected venue matches).

### Step H4.2 — Testnet transition rule

**[AGENT]** Update lifecycle state machine to permit:

```
PAPER_READY → PAPER  (existing, unchanged)
PAPER       → LIVE_READY  (existing, unchanged)
LIVE_READY  → LIVE   (existing — mainnet)
```

For HL only, the transition from `PAPER` to `LIVE_READY` may be exercised via testnet without invoking `LIVE_TRADING_CONFIRMED`. The intent: testnet acts as a real-network shakedown before mainnet, but does not itself require the mainnet env gate.

Document in `runbooks/codegen-strategy.md` (or a new `runbooks/hyperliquid-testnet.md`):

> An HL strategy advances `PAPER → LIVE_READY` once paper has met its acceptance criteria. Once in `LIVE_READY`, run on testnet for at least 24h to validate signing, rate-limit handling, and reconnect behaviour against a real exchange. Only after testnet passes should the strategy transition `LIVE_READY → LIVE` (mainnet, real money), which still requires all three live gates.

### Step H4.3 — Runner event tagging

**[AGENT]** `agent/events.py`: `runner_start`, `order_submitted`, `order_filled` events for HL include `network: "testnet" | "mainnet"`. Paper events use the configured network for the data subscription but record `is_paper: true` so downstream tooling never confuses paper-on-mainnet-data with real fills.

**Verification (H4):**
```bash
.venv/bin/python scripts/transition_lifecycle.py \
    --slug hl-smoke --to LIVE_READY --actor user:noel
# Expected: succeeds without LIVE_TRADING_CONFIRMED

.venv/bin/python scripts/transition_lifecycle.py \
    --slug hl-smoke --to LIVE --actor user:noel
# Expected: refused without LIVE_TRADING_CONFIRMED=true
```

---

## Phase H5 — End-to-end smoke

### Step H5.1 — Stub HL hypothesis

**[AGENT]** Add `research/hypotheses/hl-smoke.md`:

```yaml
---
slug: hl-smoke
venue: hyperliquid
source: manual
created: 2026-05-27
state: PAPER_READY
market_criteria:
  symbols: [BTC-PERP]
---

# HL Smoke Strategy
Single-instrument no-op strategy that places one buy 1% below mid and one
sell 1% above mid, every 60s. Purpose: exercise the full HL paper/testnet
plumbing without any real edge claim. Never promote past LIVE_READY.
```

**[AGENT]** Add `src/trading_lab/strategies/hl_smoke.py` implementing the above against `HYPERLIQUID` venue.

### Step H5.2 — Full gate run

**[YOU]**:

```bash
# 1. Paper — should run for any duration with simulated fills
make paper-hl HYPOTHESIS=hl-smoke
# Watch logs/events.jsonl: order_submitted, order_filled, network=mainnet (data only), is_paper=true

# 2. Transition to LIVE_READY
.venv/bin/python scripts/transition_lifecycle.py --slug hl-smoke --to LIVE_READY --actor user:noel

# 3. Testnet — real signing, real fills, faucet USDC
make live-hl-testnet HYPOTHESIS=hl-smoke
# Watch HL testnet UI: orders appear, fills clear, balance moves in testnet USDC

# 4. (DO NOT) advance to LIVE. hl-smoke is documented as never-promotable.
```

**Phase H5 gate check — [YOU]:**

- `make paper-hl HYPOTHESIS=hl-smoke` runs ≥10 minutes and produces ≥1 `order_filled` event
- `make live-hl-testnet HYPOTHESIS=hl-smoke` runs ≥10 minutes and at least one order appears on `app.hyperliquid-testnet.xyz` for your wallet, with at least one fill
- `make check-env --network testnet` is green
- All existing 112 tests still pass; new HL paper-fill tests pass

---

## Out of scope (explicitly)

- **HL backtesting.** No Parquet adapter for HL data, no `BacktestRunner` wiring for HL. Defer until a real HL strategy is proposed.
- **Real HL strategies.** `hl-smoke` is plumbing only. Edge-claim strategies are separate work, gated through the normal autoresearch loop.
- **Cross-venue strategies that touch both PM and HL in one TradingNode.** The architectural commitment is one strategy per TradingNode process; cross-venue arbitrage would need a separate coordination layer designed deliberately. `strategies/cross_venue_hedge.py` exists as a stub but is not exercised by this spec.
- **Mainnet capital deployment.** This spec stops at testnet. Going live on HL mainnet is a separate operator decision that re-uses the existing live-trading triple-gate.
- **Vault / sub-account routing.** `HL_ACCOUNT_ADDRESS` is wired through `auth.py` but vault-specific flows (deposit, withdraw permissions) are not validated here.

---

## Risks and known gaps

- **Testnet liquidity is thin.** Fills on testnet can take longer to clear than on mainnet and price discovery is noisier; a strategy that passes testnet does not automatically pass mainnet, and vice versa. Testnet's job here is integration validation, not strategy validation.
- **HL meta-cache.** `HyperliquidRestClient._meta_cache` is per-instance; restarting the runner re-fetches. Asset indices can shift if HL adds/removes listings — the cache should be invalidated on each `_connect`, not just on cold start. Track as follow-up; not in scope here unless tests reveal it.
- **EIP-712 domain `chainId=1337`.** Hardcoded in `auth.py:_HL_DOMAIN`. HL has historically kept this constant for both networks; verify in Step H1 before signing real testnet actions, and lift to config if HL splits the domain in future.
- **Paper fill engine fee assumption.** Bakes in HL's current public taker tier. If your account qualifies for a different tier, paper PnL will diverge from real PnL by the fee delta — document in the hypothesis's expected-edge calculation.
