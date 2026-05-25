# Runbook: Walk-forward optimise

**You are the optimise agent.** Take one hypothesis in OPTIMIZE state,
run the grid + walk-forward, and let `optimize_strategy.py` transition it
to PAPER_READY / SHELVED / REJECTED.

## Pre-conditions

```bash
.venv/bin/python scripts/research_cli.py budget
```
Stop if `backtests` ≥ 50.

The hypothesis MD must have a `## Parameter space` section listing
`<name>: [v1, v2, v3]` lines (numbers only). If it doesn't, the optimise
script fails with `error: no_parameter_space`. Read the MD first:

```bash
cat research/hypotheses/<slug>.md
```

## Inputs

Invoked with `--slug <slug> --data-start <YYYY-MM-DD> --data-end
<YYYY-MM-DD>`. Pick a data range that:
- the catalog has data for (check `DataCatalog.list_available_markets`),
- is long enough that `3 walk-forward windows of >= 30 trades each` is plausible,
- ends within the last 7 days (so the recent-regime window has signal).

## Steps

1. Run:
   ```bash
   .venv/bin/python scripts/optimize_strategy.py \
       --slug <slug> --data-start <start> --data-end <end> \
       --n-windows 3 --top-k 2
   ```

   This:
   - Reads `## Parameter space` from the MD.
   - Runs the cartesian-product grid on the training portion (first 70%).
   - Takes top-K by training PnL.
   - For each candidate, runs the WF (3 windows; one forced to be the
     last 30 days).
   - Picks the winner by `(recent_oos_pnl, oos_mean_pnl)`.
   - Applies decision rules and transitions.

2. Parse the JSON:
   - `decision_new_state`: PAPER_READY | SHELVED | REJECTED
   - `best_params`: the winning param set
   - `best_oos_mean_sharpe`, `best_recent_oos_pnl`, etc.

3. If `decision_new_state == "PAPER_READY"`, write a brief summary to
   the hypothesis MD (append section) with the chosen `best_params` so
   the human reviewer sees what to deploy:

   ```bash
   cat >> research/hypotheses/<slug>.md <<EOF

   ## Optimised parameters (PAPER_READY)
   - best_params: $(jq -c .best_params <(echo '<the json>'))
   - oos_mean_sharpe: ...
   - recent_oos_pnl: ...
   EOF
   ```

4. Verify:
   ```bash
   .venv/bin/python scripts/research_cli.py show --slug <slug> | jq '.state'
   ```

## Decision rules summary (optimize_strategy.py applies these)

| Condition | New state | Category |
|---|---|---|
| `n_trades_clearing_floor` empty | REJECTED | insufficient_trades |
| `oos_mean_sharpe < 0.7 AND NOT pnl_positive_in_all_active_windows` | REJECTED | overfit |
| `recent_oos_sharpe < 0.7 AND recent_oos_pnl <= 0` | SHELVED | regime_change |
| `oos_mean_sharpe < 1.0 AND NOT (pnl_positive_all AND total >= 100)` | SHELVED | marginal_oos |
| `is_sharpe > 0 AND oos_mean_sharpe < 0.6 * is_sharpe` | SHELVED | is_oos_gap |
| `pnl_positive_all AND total >= 100` OR `oos_mean_sharpe >= 1.0` | PAPER_READY | — |

## Hard rules

- **Do not transition past PAPER_READY here.** `PAPER_READY → PAPER` is
  human-only.
- **Do not modify the parameter space** to make a hypothesis pass. If you
  think the space is too narrow, REJECT/SHELF and explain.
- **Do not skip the recent-regime window.** The script enforces it; don't
  pass `--n-windows 1` to dodge it.

## Success criteria

- One JSON line on stdout with `ok: true`.
- `research_cli.py show --slug <slug>` shows the new state.
- Multiple new rows in `experiments` (one per grid point + one per WF
  window per candidate).

## Output format

```json
{"ok": true, "slug": "...", "new_state": "PAPER_READY", "best_params": {...}, "best_recent_oos_pnl": ...}
```
