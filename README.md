# Nautilus-Predict

Algorithmic trading lab for [Polymarket](https://polymarket.com) (prediction
markets) and [Hyperliquid](https://hyperliquid.xyz) (perp DEX), built on
[NautilusTrader](https://nautilustrader.io). Three runtimes — **backtest /
paper / live** — that share the same strategy code paths.

## What runs today

| Layer | Status |
|---|---|
| **Data ingestion** — historical fetch (data-api) + live WS daemon + sqlite metadata + Parquet catalog | ✅ |
| **Backtest** — NT `BacktestEngine` with realistic FillModel + LatencyModel; per-pair Sharpe; parallel grid + walk-forward optimisation | ✅ |
| **Paper trading** — real NT `TradingNode` with `is_paper=True` + `PolymarketPaperFillEngine` Actor; same code path as live | ✅ |
| **Live trading** — same TradingNode, `is_paper=False`; pre-flight refuses without env + creds + clear kill switch + state=LIVE | ✅ (untested with real capital) |
| **Agentic loop** — sqlite experiment DB, lifecycle state machine, codegen guards, JSON-I/O CLI surface, 4 runbooks | ✅ |
| **Operator harness** — `logs/events.jsonl` + briefing script with built-in forwarding policy; ready for an external SMS/Slack/email agent | ✅ |
| **Risk layer** — persistent kill switch, heartbeat watcher, position limits, paper auto-retirement (5%/15% rules) | ✅ |

For a deep-dive on the architecture, see [docs/architecture.md](docs/architecture.md),
[docs/agentic-loop.md](docs/agentic-loop.md), and
[docs/deployment.md](docs/deployment.md).

## Strategies in this repo

| Strategy | File | Status |
|---|---|---|
| `BinaryArbStrategy` | `strategies/arb_complement.py` | PAPER, optimised |
| `TickMeanRevertStrategy` | `strategies/tick_mean_revert.py` | PAPER, optimised |
| `WideSpreadFadeStrategy` | `strategies/wide_spread_fade.py` | PAPER (override) |
| `PolymarketMarketMaker` | `strategies/market_maker.py` | Scaffolded, not wired |
| `CrossVenueHedgeStrategy` | `strategies/cross_venue_hedge.py` | Scaffolded |
| `CatalystTrader` | `strategies/catalyst_trader.py` | Scaffolded |

## Fresh-machine setup

See [docs/getting-started.md](docs/getting-started.md) for the full
walkthrough. The short version:

```bash
# 1. Clone + install
git clone <repo-url> trading-lab && cd trading-lab
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv
make dev                                          # creates .venv + installs deps

# 2. Credentials (paper trades work with empty L2 creds; need them for live)
cp .env.example .env
$EDITOR .env                                      # paste POLY_PRIVATE_KEY
.venv/bin/python scripts/derive_polymarket_keys.py  # one-time L2 derivation
make check-env                                    # 23/23 should pass

# 3. Sync market metadata (~10s)
make sync-markets

# 4. Backfill historical trades for the markets you want to backtest
.venv/bin/python scripts/download_polymarket_data.py \
    --condition-id 0xa70fc3695a65833b91b45df6db6015096f3e1471b70352ca411b4209010e7633 \
    --start 2026-05-10 --end 2026-05-26

# 5. Init the agentic-loop DB + register the seed hypothesis
.venv/bin/python scripts/research_cli.py init
.venv/bin/python scripts/propose_hypothesis.py \
    --file research/hypotheses/arb-complement.md --initial-state BACKTEST

# 6. Eval + optimise → PAPER_READY
make research-test     SLUG=arb-complement START=2026-05-24 END=2026-05-26
make research-optimize SLUG=arb-complement START=2026-05-24 END=2026-05-26

# 7. Approve the human gate (PAPER_READY → PAPER)
.venv/bin/python scripts/transition_lifecycle.py \
    --slug arb-complement --to PAPER \
    --reason "human approves paper deployment" --actor user:$USER

# 8. Paper-trade for 5 minutes
make paper-run SLUG=arb-complement DURATION_SECS=300
```

## Daily operations

```bash
# Continuous data capture (long-lived; usually under systemd/tmux)
make data-ingest

# Periodic re-eval on the rolling window (cron-friendly)
make rolling-eval

# Per-slug paper PnL report (writes research/paper_reports/<slug>_<date>.md)
make paper-summary SLUG=tick-mean-revert

# Auto-retirement watcher — halt/retire PAPER strategies on threshold breaches
make paper-watcher

# Operator briefing — JSON for your SMS/Slack agent; --md for human-readable
make operator-brief MD=1

# Inspect lifecycle state
make research-status                       # all hypotheses
make research-status SLUG=tick-mean-revert # one slug + history + experiments
```

## Trading venues

### Polymarket — primary
- Central Limit Order Book on Polygon, binary outcome tokens
- EIP-712 L1 auth → derived L2 API credentials (HMAC-SHA256)
- `gamma-api.polymarket.com` (metadata), `data-api.polymarket.com` (history), `clob.polymarket.com` (book/orders), `wss://ws-subscriptions-clob.polymarket.com/ws/{market,user}` (live)
- See [docs/polymarket_auth.md](docs/polymarket_auth.md) for auth details

### Hyperliquid — secondary
- Perp futures DEX
- Auth + client scaffolding present but no strategy uses it yet

## Safety

- **Kill switch:** persists to `data/.kill_switch`; tripping from any process halts all paper/live runners on next watcher tick.
  ```bash
  scripts/halt_trading.py --reason "..."     # trip
  scripts/reset_kill_switch.py --confirm     # clear
  ```
- **Live trading triple-gate:** requires `TRADING_MODE=live` AND `LIVE_TRADING_CONFIRMED=true` AND hypothesis state=LIVE.
- **Auto-retirement watcher:** PAPER strategies → HALTED on single-day -5%; → RETIRED on 7d -15%.
- **Heartbeat monitor:** trips the kill switch on connection timeout.
- **Position limits:** per-market USDC caps via `RiskConfig`.
- **Lifecycle human gates:** `PAPER_READY → PAPER` and `LIVE_READY → LIVE` refuse non-`user:*` actors.

Default mode is **paper**. Live trading requires explicit triple opt-in.

## Agentic loop

Every script under `scripts/` is JSON-in/JSON-out with explicit exit codes,
designed to be driven by an external agent runtime (Claude Code or any LLM
with shell access). See [docs/agentic-loop.md](docs/agentic-loop.md) for the
full architecture + skill/connector matrix, and [runbooks/](runbooks/) for
agent-facing prompts:

- `runbooks/discover-strategies.md` — drain `manual_inbox/` + RSS → PROPOSED
- `runbooks/codegen-strategy.md` — write strategy code + smoke test
- `runbooks/test-strategy.md` — eval + decision rules
- `runbooks/optimize-strategy.md` — walk-forward + recent-regime gate
- `runbooks/onboard-existing-strategy.md` — register a hand-written strategy

State lives in `research/experiments.db` (sqlite). The only writer to
`hypotheses.state` and `lifecycle_transitions` is
`src/nautilus_predict/agent/lifecycle.py` — every transition is logged
with `from_state, to_state, reason, actor`.

## Operator harness (planned external agent)

Every state change, watcher decision, kill-switch trip, and paper-summary
delta writes a structured event to `logs/events.jsonl`. The companion
script `scripts/operator_briefing.py` reads from a byte-offset cursor,
applies a forwarding policy (all `critical` + dedup'd `warn` per type/slug
+ paper-PnL deltas), and returns JSON the external agent forwards as
SMS / Slack / email.

The transport (Twilio / Slack / etc.) is NOT in this repo — it's a tiny
~30-line wrapper that lives on your deployment machine. Recipe in
[docs/deployment.md](docs/deployment.md).

## Requirements

- Python 3.12+
- uv (for venv management; `make dev` installs deps via uv)
- Rust 1.75+ (optional — for `polyfill-rs`; not yet integrated)
- Docker (optional — only if you want the container deployment path)

## License

MIT
