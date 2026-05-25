# Runbook: Drain BACKTEST queue (testing loop)

**For agents.** Iterate over hypotheses in state BACKTEST, run
`eval_strategy.py` for each, and apply the decision-rule transition.

## Before starting

1. Check budget:
   ```bash
   .venv/bin/python scripts/research_cli.py budget
   ```
   Exit early if `backtests >= 50` (the default daily cap).

2. Ensure historical data is on disk for the markets the hypothesis selects.
   `eval_strategy.py` will return zero trades if `data/parquet/<token>/`
   directories are empty. If empty:
   ```bash
   .venv/bin/python scripts/sync_market_metadata.py --active-only
   # then for each selected condition_id (use research_cli + select_markets):
   .venv/bin/python scripts/download_polymarket_data.py \
       --condition-id 0x... --start 2026-05-10 --end 2026-05-26
   ```

## Drain loop

For each hypothesis with state=BACKTEST:

```bash
SLUG=$(.venv/bin/python scripts/research_cli.py list --state BACKTEST \
       | jq -r '.[0].slug')

.venv/bin/python scripts/eval_strategy.py --slug "$SLUG" \
    --start 2026-05-10 --end 2026-05-26
```

`eval_strategy.py` prints JSON:
- `ok=true, applied=true`: hypothesis transitioned to OPTIMIZE / SHELVED / REJECTED
- `ok=false, error="budget_exhausted"`: stop and try again tomorrow
- `ok=false, error="hypothesis_not_found"`: log and skip

## Decision rules summary

| Sharpe | Max DD | n_trades | New state | Category |
|---|---|---|---|---|
| any | any | < 30 | REJECTED | insufficient_trades |
| < 0 | any | ≥ 30 | REJECTED | unprofitable |
| 0–0.5 | any | ≥ 30 | SHELVED | marginal_is |
| 0.5–1.0 | > 25% | ≥ 30 | REJECTED | high_dd |
| 0.5–1.0 | ≤ 25% | ≥ 30 | SHELVED | marginal_is |
| ≥ 1.0 | > 20% | ≥ 30 | REJECTED | high_dd |
| ≥ 1.0 | ≤ 20% | ≥ 30 | OPTIMIZE | — |
