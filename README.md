# Nautilus-Predict

Algorithmic trading lab for [Polymarket](https://polymarket.com) (prediction
markets) and [Hyperliquid](https://hyperliquid.xyz) (perp DEX), built on
[NautilusTrader](https://nautilustrader.io). Strategy: complement arbitrage
on binary prediction markets — buy YES + NO when the combined ask falls below
$1.00 (minus fees), hold to resolution.

## What runs today

- **Data:** historical trade ingestion + sqlite market metadata + parquet catalog
- **Backtest:** NautilusTrader `BacktestEngine` with realistic FillModel + LatencyModel; hypothesis-driven market selection; per-market + aggregate metrics
- **Paper:** live Polymarket WS stream, in-process simulated fills, jsonl trade log, kill-switch wired
- **Agentic layer:** sqlite experiment DB + lifecycle state machine + AST codegen guards + JSON-I/O CLI surface for an external agent runtime
- **Risk:** persistent kill-switch (`data/.kill_switch`), heartbeat watcher, position limits

`make backtest HYPOTHESIS=arb-complement` validates the strategy on
hypothesis-selected markets. Verified profitable on balanced markets (e.g.
US-Iran nuclear deal: +$13.86 over 97 paired arbs, ~$0.14/arb edge).

## Quick start

```bash
# 1. One-time bootstrap
make dev                # uv venv + install deps

# 2. Configure credentials (paper mode works with empty creds for read-only)
cp .env.example .env
# fill POLY_PRIVATE_KEY + derived L2 creds (see `make derive-keys`)

# 3. Sync market metadata + download recent data
make sync-markets
.venv/bin/python scripts/download_polymarket_data.py \
    --condition-id 0xa70fc3695a65833b91b45df6db6015096f3e1471b70352ca411b4209010e7633 \
    --start 2026-05-10 --end 2026-05-26

# 4. Backtest (hypothesis-driven)
.venv/bin/python scripts/backtest.py --hypothesis-slug arb-complement \
    --start 2026-05-10 --end 2026-05-26

# 5. Paper trade (live WS, simulated fills)
.venv/bin/python -m nautilus_predict.main --mode paper --duration-secs 300

# 6. Inspect the agentic layer
.venv/bin/python scripts/research_cli.py init
.venv/bin/python scripts/propose_hypothesis.py \
    --file research/hypotheses/arb-complement.md --initial-state BACKTEST
.venv/bin/python scripts/eval_strategy.py --slug arb-complement \
    --start 2026-05-10 --end 2026-05-26
.venv/bin/python scripts/research_cli.py show --slug arb-complement
```

## Trading venues

### Polymarket — primary
- Central Limit Order Book on Polygon
- Binary outcome prediction markets (YES/NO tokens)
- EIP-712 L1 auth → derived L2 API credentials (HMAC-SHA256)
- WebSocket feeds for real-time book + trade prints
- `gamma-api.polymarket.com` for market metadata, `data-api.polymarket.com` for trade history, `clob.polymarket.com` for book/orders

### Hyperliquid — secondary
- Perp futures DEX; auth + client scaffolding present but no strategy uses it yet

## Strategies

| Strategy | Description | Status |
|----------|-------------|--------|
| `BinaryArbStrategy` | YES + NO < $1.00 - fees → buy both legs | Backtested + paper-runnable |
| `PolymarketMarketMaker` | Quote both sides, earn rebates | Scaffolded, not wired |
| `CrossVenueHedgeStrategy` | Hyperliquid/Polymarket arb | Scaffolded |
| `CatalystTrader` | Crypto catalyst momentum | Scaffolded |

## Implementation phases

See `specs/2026-05-24_bootstrap.md` for the full plan. Status snapshot:

| Phase | State |
|---|---|
| 0 — Foundation | ✅ |
| 0.5 — uv environment | ✅ |
| 0.6 — Persistent KillSwitch | ✅ |
| 1 — Data infra | ✅ |
| 1.6 — Market metadata | ✅ |
| 2 — Backtesting | ✅ |
| 3 — Paper trading | 🟡 lightweight harness (full NT TradingNode wiring deferred) |
| 4 — Live trading | ❌ |
| 5 — Agentic layer | 🟢 foundation (lifecycle + DB + CLI + 3 runbooks); discovery/walk-forward TBD |

## Safety

- **Kill switch:** persists to `data/.kill_switch`; tripping from any process halts all runners. `scripts/halt_trading.py --reason "<text>"` and `scripts/reset_kill_switch.py --confirm` wrap it.
- **Heartbeat monitor:** trips the kill switch on connection timeout.
- **Position limits:** per-market USDC caps via `RiskConfig`.
- **Live trading double opt-in:** requires both `TRADING_MODE=live` and `LIVE_TRADING_CONFIRMED=true`.
- **Lifecycle human gates:** `PAPER_READY → PAPER` and `LIVE_READY → LIVE` refuse any actor not starting with `user:`.

Default mode is **paper** — live trading requires explicit configuration.

## Agentic layer

The repo exposes a CLI surface designed for agentic use: every script
prints JSON to stdout, takes argparse args, and exits 0/non-zero. An
external agent runtime can drive these via runbooks at `runbooks/*.md`:

- `runbooks/onboard-existing-strategy.md` — register a hand-written strategy
- `runbooks/codegen-strategy.md` — drain `PROPOSED` queue (untrusted-input safe)
- `runbooks/test-strategy.md` — drain `BACKTEST` queue + apply decision rules

State lives in `research/experiments.db` (sqlite). The only module that
writes `hypotheses.state` is `src/nautilus_predict/agent/lifecycle.py` —
every transition is logged with `from_state, to_state, reason, actor`.

## Requirements

- Python 3.12+
- Rust 1.75+ (for `polyfill-rs`; optional, not yet integrated)
- Docker (optional)

## License

MIT
