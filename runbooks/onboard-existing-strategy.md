# Runbook: Onboard an Existing Strategy

**For agents.** Use this one-shot to register a hand-written strategy into the
research lifecycle DB. Common case: `BinaryArbStrategy` already exists in
`src/nautilus_predict/strategies/arb_complement.py` and its hypothesis MD is
at `research/hypotheses/arb-complement.md` — we want it visible to
`research_cli.py` and ready for `eval_strategy.py`.

## Steps

1. **Init DB if missing:**
   ```bash
   .venv/bin/python scripts/research_cli.py init
   ```

2. **Register the hypothesis:**
   ```bash
   .venv/bin/python scripts/propose_hypothesis.py \
       --file research/hypotheses/arb-complement.md \
       --initial-state BACKTEST
   ```
   `--initial-state BACKTEST` skips codegen/smoke since the strategy code
   already exists and is human-written.

3. **Verify:**
   ```bash
   .venv/bin/python scripts/research_cli.py show --slug arb-complement
   ```
   Expect `state: BACKTEST` and a history entry created by step 2.

4. **Drive the first evaluation:**
   ```bash
   .venv/bin/python scripts/eval_strategy.py --slug arb-complement \
       --start 2026-05-10 --end 2026-05-26
   ```
   This writes an experiments row and (if decision rules pass) transitions
   the hypothesis to OPTIMIZE.

## Don't

- **Don't** edit a strategy file in place after registering. If you want to
  change parameters, create a new hypothesis slug with `parent_slug` set to
  the original; the original stays REJECTED/SHELVED with its history intact.
- **Don't** call `lifecycle.transition()` from outside this runbook for the
  `PAPER_READY → PAPER` or `LIVE_READY → LIVE` gates — those are human-only.
