# Runbook: Run a backtest + apply decision rules

**You are the test agent.** Take one hypothesis in SMOKE_PASS or BACKTEST
state, run `eval_strategy.py`, let it transition the hypothesis to
OPTIMIZE / SHELVED / REJECTED based on the result. Nothing else.

## Pre-conditions

```bash
# Check budget — exit early if exhausted.
.venv/bin/python scripts/research_cli.py budget
```

If `backtests` ≥ 50 (default daily cap), print `{"ok": false, "error":
"budget_exhausted"}` and stop. Try again tomorrow.

## Required inputs

You will be invoked with `--slug <slug> --start <YYYY-MM-DD> --end
<YYYY-MM-DD>`.

Before running, verify the hypothesis's selected markets have data:

```bash
.venv/bin/python -c "
from pathlib import Path
from nautilus_predict.data.market_catalog import MarketCatalog
from nautilus_predict.data.market_filter import MarketCriteria, select_markets
from nautilus_predict.agent import lifecycle
from nautilus_predict.data.catalog import DataCatalog

h = lifecycle.get_hypothesis('<slug>')
crit = MarketCriteria.from_dict(h.market_criteria)
cat = MarketCatalog(Path('data/market_catalog.db'))
rows = select_markets(crit, cat)
dc = DataCatalog(Path('data/parquet'))
on_disk = set(dc.list_available_markets())
missing = [r.condition_id for r in rows if r.yes_token_id not in on_disk]
print('missing:', missing)
"
```

If any selected markets lack on-disk data, fetch them first:

```bash
.venv/bin/python scripts/download_polymarket_data.py \
    --condition-id <each missing condition_id> \
    --start <start> --end <end>
```

## Steps

1. Run eval:
   ```bash
   .venv/bin/python scripts/eval_strategy.py \
       --slug <slug> --start <start> --end <end>
   ```

2. Parse the JSON. Expected fields: `decision_new_state`,
   `decision_rejection_category`, `applied`, `pnl_usdc`, `sharpe`,
   `n_trades`, `experiment_id`.

3. Verify the transition was applied:
   ```bash
   .venv/bin/python scripts/research_cli.py show --slug <slug> | jq '.state'
   ```
   The state must match `decision_new_state` from step 2.

## Decision rules summary (eval_strategy.py applies these automatically)

| Condition | New state | Category |
|---|---|---|
| `n_trades < 30` | REJECTED | insufficient_trades |
| `pnl < 0` | REJECTED | unprofitable |
| `pnl > 0 AND sharpe < 0 AND n_trades >= 100` | OPTIMIZE | (hold-to-resolution sharpe artefact) |
| `sharpe < 0.5` | SHELVED | marginal_is |
| `sharpe < 1.0 AND max_dd > 25%` | REJECTED | high_dd |
| `sharpe < 1.0` | SHELVED | marginal_is |
| `sharpe >= 1.0 AND max_dd > 20%` | REJECTED | high_dd |
| otherwise | OPTIMIZE | — |

## Hard rules

- **Do not transition past OPTIMIZE.** Walk-forward optimisation is a
  separate runbook.
- **Do not adjust the decision rules** to make a hypothesis pass. If you
  think the rules are wrong, file a follow-up and stop.
- **Do not re-run a hypothesis that's already in a terminal state**
  (REJECTED, SHELVED, OPTIMIZE, PAPER_READY, PAPER, LIVE, RETIRED). Only
  re-run when state is SMOKE_PASS or BACKTEST.

## Success criteria

- New row in `experiments` table.
- Hypothesis state advanced to one of: OPTIMIZE, SHELVED, REJECTED.
- The state shown by `research_cli.py show` matches `decision_new_state`.

## Output format

```json
{"ok": true, "slug": "...", "experiment_id": N, "new_state": "OPTIMIZE", "pnl_usdc": ..., "n_trades": ...}
```
