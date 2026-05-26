# Getting started — fresh-machine setup

Written so that an LLM-driven agent (or you, with one) can take a
**blank Linux/macOS box** to a running paper trade in ~15 minutes.

Every step prints something specific you can verify before moving on. If a
step prints something different than what's documented, STOP — don't paper
over it.

---

## 0. Prerequisites — what you need before you start

- **Python 3.12 or later** — `python3 --version` ≥ 3.12
- **git**
- **A Polymarket wallet** (Ethereum/Polygon address) funded with at least
  $5 USDC for any test L2 derivation. For paper-only trading you can even
  skip the derived L2 creds — see step 3 below.

Optional but recommended:
- **uv** (Python venv + dep manager — much faster than vanilla pip).
  Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **jq** (for parsing JSON outputs in shell).

---

## 1. Clone + install

```bash
git clone <repo-url> trading-lab
cd trading-lab
make dev
```

`make dev` creates `.venv/` via uv and installs all deps including dev
tools. First run takes 2-3 min (NautilusTrader has a large C extension).

**Verify:**
```bash
.venv/bin/python -c "import nautilus_trader; print(nautilus_trader.__version__)"
```
Expected: `1.227.0` or later.

```bash
make test
```
Expected: `98 passed` (or more — test count grows over time).

---

## 2. Credentials

Copy the example env file and edit it:

```bash
cp .env.example .env
$EDITOR .env
```

**For PAPER trading only**, you need at minimum:

```
TRADING_MODE=paper
POLY_PRIVATE_KEY=0x<your wallet private key>
```

L2 API credentials (`POLY_API_KEY/SECRET/PASSPHRASE`) can stay blank for
backtest + paper modes — the data fetch + WS subscription paths use only
public endpoints.

**For LIVE trading**, all of the above PLUS:
- `LIVE_TRADING_CONFIRMED=true` (the second of two opt-ins)
- L2 creds (derived below)
- `MAX_POSITION_USDC` and `DAILY_LOSS_LIMIT_USDC` set conservatively

---

## 3. Validate environment

```bash
make check-env
```

Expected: `23/23 checks PASSED`. If anything fails, the message tells you
what's missing.

**Common failure**: `polymarket-connectivity FAILED` — usually means
firewall or no internet. The agent should retry once before reporting.

---

## 4. (Optional) Derive L2 API credentials

Only needed if you intend to do **live** trading or query authenticated
endpoints. For paper trading you can skip this entirely.

```bash
.venv/bin/python scripts/derive_polymarket_keys.py
```

Expected: JSON line with `api_key / api_secret / api_passphrase`.
**Paste those three values back into `.env`** (the relevant variables).

---

## 5. Sync market metadata

```bash
make sync-markets
```

Expected: JSON line like `{"fetched": 5000, "upserted": 5000, ...}` — the
script writes `data/market_catalog.db` with all currently-active Polymarket
binary markets.

For a full sync (including closed/archived markets, ~50 MB):
```bash
make sync-markets-full
```

---

## 6. Initialise the research lifecycle DB

```bash
.venv/bin/python scripts/research_cli.py init
```

Expected: `{"db": "research/experiments.db", "initialized": true}`

Then register the seed hypothesis (binary complement arb on PM):

```bash
.venv/bin/python scripts/propose_hypothesis.py \
    --file research/hypotheses/arb-complement.md \
    --initial-state BACKTEST
```

Verify:
```bash
make research-status SLUG=arb-complement
```
Expected: state `BACKTEST`, history with 1 entry.

---

## 7. Backfill historical trade data

Pick one of the markets the seed hypothesis would select. The arb-complement
hypothesis selects balanced binary markets with > $20k 24h volume; an
example condition_id is the US-Iran nuclear deal market:

```bash
.venv/bin/python scripts/download_polymarket_data.py \
    --condition-id 0xa70fc3695a65833b91b45df6db6015096f3e1471b70352ca411b4209010e7633 \
    --start 2026-05-10 --end 2026-05-26
```

Expected: `Total markets: 2 ... Total Parquet files: 4+` (numbers vary).
Files land in `data/parquet/<token_id>/trades/`.

For a broader test, fetch a few more markets that match the hypothesis:
```bash
.venv/bin/python -c "
from pathlib import Path
from nautilus_predict.data.market_catalog import MarketCatalog
from nautilus_predict.data.market_filter import MarketCriteria, select_markets
crit = MarketCriteria(outcome_type='binary', min_volume_24h_usdc=20000,
                       yes_prob_range=(0.30, 0.70), resolved=False, count=5)
cat = MarketCatalog(Path('data/market_catalog.db'))
for r in select_markets(crit, cat):
    print(r.condition_id)
"
# Then run download_polymarket_data.py for each printed condition_id.
```

---

## 8. Run a backtest

```bash
make research-test SLUG=arb-complement START=2026-05-24 END=2026-05-26
```

Expected: JSON with `ok: true, decision_new_state: OPTIMIZE` (assuming the
strategy is profitable on the data window). If you see `REJECTED` instead,
that's still a successful run — the eval did its job.

Inspect:
```bash
make research-status SLUG=arb-complement
```

---

## 9. Walk-forward optimise

```bash
make research-optimize SLUG=arb-complement START=2026-05-24 END=2026-05-26
```

Runs 9 grid points × 3 walk-forward windows in parallel. Expected: JSON
with `decision_new_state: PAPER_READY` and `best_params: {...}`. Takes
~30s with 4 workers.

---

## 10. Promote to PAPER (human gate)

PAPER_READY → PAPER is a HUMAN-gated transition. Agents cannot do it.

```bash
.venv/bin/python scripts/transition_lifecycle.py \
    --slug arb-complement --to PAPER \
    --reason "human approves paper deployment" --actor user:$USER
```

Expected: `{"ok": true, "to_state": "PAPER", "actor": "user:..."}`

---

## 11. First paper run (5 minutes)

```bash
make paper-run SLUG=arb-complement DURATION_SECS=300
```

Expected output sequence:
1. `PaperRunnerV2 starting | slug=arb-complement strategy=BinaryArbStrategy ...`
2. `Polymarket WebSocket connected ...`
3. `PM ws sending N queued subscription(s)`
4. `PM ws msg #1 type=book ...`
5. After 5 minutes: `{"ok": true, "slug": "arb-complement", ...}`

Arb opportunities are rare in liquid markets — you may see 0 paper fills
in a 5-minute window. That's correct behavior. To see the system
ACTIVELY firing for validation purposes, run `tick-mean-revert` instead
(it fires on every book tick):

```bash
.venv/bin/python scripts/transition_lifecycle.py \
    --slug tick-mean-revert --to PAPER \
    --reason "validation run" --actor user:$USER
make paper-run SLUG=tick-mean-revert DURATION_SECS=300
```

---

## 12. Summarise + brief

After the paper run:

```bash
make paper-summary SLUG=arb-complement
```
Writes `research/paper_reports/arb-complement_<date>.md` with realised
PnL, win rate, per-token breakdown.

Run the watcher (idempotent — safe to re-run anytime):
```bash
make paper-watcher
```

Get an operator briefing (the same format an external SMS agent would
ingest):
```bash
make operator-brief MD=1
```

---

## 13. Continuous operation (optional — usually deployed on a server)

Three long-running processes:

1. **Data ingestion** — keeps catalog fresh for rolling-eval:
   ```bash
   make data-ingest
   ```

2. **Paper run** — one per PAPER strategy:
   ```bash
   make paper-run SLUG=arb-complement DURATION_SECS=0   # 0 = run forever
   ```
   In practice you'd run these under systemd. See
   [docs/deployment.md](deployment.md) for the unit-file sketch.

3. **Cron jobs** — watcher every 10 min, paper-summary hourly,
   rolling-eval every few hours. See [docs/scheduling.md](scheduling.md)
   for cron recipes.

---

## Going live (when you're ready)

Pre-flight (no money risk — just checks):

```bash
TRADING_MODE=live LIVE_TRADING_CONFIRMED=true make live-run SLUG=arb-complement
```

This dry-runs all the gates and prints what would happen. If everything
passes, then to actually trade real money:

```bash
TRADING_MODE=live LIVE_TRADING_CONFIRMED=true \
    make live-run SLUG=arb-complement CONFIRM=1
```

The `CONFIRM=1` passes `--i-understand-this-is-live` to the script.

The hypothesis must also be in `LIVE` state. To get there from PAPER:
```bash
.venv/bin/python scripts/transition_lifecycle.py \
    --slug arb-complement --to LIVE_READY \
    --reason "promoting after 24h+ clean paper" --actor user:$USER
# (24h+ paper history + reviewed via operator-brief recommended first)
.venv/bin/python scripts/transition_lifecycle.py \
    --slug arb-complement --to LIVE \
    --reason "deploy with capital cap $X" --actor user:$USER
```

---

## Where to look when things break

| Symptom | Where to look |
|---|---|
| Script crashes | `--verbose` flag on most scripts; stderr is rich |
| Strategy not firing in paper | `make operator-brief MD=1` shows event flow; check `logs/events.jsonl` |
| Kill switch tripped unexpectedly | `cat data/.kill_switch` shows reason + actor + ts |
| Tests failing | `make test 2>&1 | tail -20` |
| WS connecting/disconnecting | Run with `-v` and grep for `WebSocket connected\|disconnected` |
| Wrong state on a hypothesis | `scripts/research_cli.py history --slug <slug>` |
| New strategy code looks like it didn't load | Check `research/hypotheses/<slug>.md` has `strategy_module/class/config_class` set |

For deeper architecture: [docs/architecture.md](architecture.md),
[docs/agentic-loop.md](agentic-loop.md). For agent runtime + cron
deployment: [docs/deployment.md](deployment.md). For coaching agents
through specific tasks: [runbooks/](../runbooks/).
