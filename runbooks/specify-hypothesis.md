# Runbook: Specify one hypothesis (memo → spec.md)

**You are the spec-writer agent.** You take ONE `MEMO_READY` ingestion
row whose `thesis_slug` is already assigned and produce a codegen-ready
`spec.md`.

This is the artifact `discover_strategies.py` will validate to promote
the idea into `PROPOSED`. If you cannot make the strategy concrete,
stop and reject upstream — do not paper over vagueness.

## Pre-conditions

```bash
.venv/bin/python scripts/ingestion_status.py show --slug <thesis_slug>
cat research/hypotheses/<thesis_slug>/memo.md
cat research/hypotheses/<thesis_slug>/dossier.md   # back-reference
```

Scaffold the spec:

```bash
.venv/bin/python scripts/write_hypothesis_spec.py --slug <thesis_slug>
```

## Required sections (every section must be filled and concrete)

The validator enforces presence + non-emptiness, but you should also
self-audit for vagueness. Every section below must let another agent
implement the strategy without re-deriving rules from the transcript.

- **Hypothesis** — one paragraph: what edge are we trying to capture?
- **Market criteria** — explicit predicates that select trade-able
  Polymarket markets (resolution timing, min-volume, max-spread,
  category filters).
- **Signal definition** — the observable that triggers attention.
  Concrete: "best-bid price for outcome YES exceeds 0.85 for ≥ N
  consecutive ticks." Not "use indicators."
- **Entry rule** — the exact condition that opens a position. State
  the side, the order type, and any sequencing requirements.
- **Exit rule** — the condition(s) that close it. Cover happy-path
  and stop-out paths separately.
- **Sizing rule** — explicit position sizing formula (fraction of
  equity, fixed notional, Kelly, etc.). No "size appropriately."
- **Risk controls** — hard stops, max drawdown thresholds, kill-switch
  triggers, max concurrent positions, per-market exposure caps.
- **Required data** — list every input the strategy needs that we
  don't already have wired up. If any are missing, prefer to reject
  rather than ship a strategy that silently can't run.
- **Parameter space** — explicit dictionary mapping parameter name
  to allowed values/ranges, including the default. The
  optimization runner needs to parse this.
- **Acceptance criteria** — backtest thresholds (sharpe, max_dd,
  fill_rate, etc.) that must hold before the strategy can move past
  `BACKTEST`. Be quantitative.

## Hard rules

- No vague verbs: `monitor`, `consider`, `use indicators`,
  `when appropriate`. Replace each with a measurable condition.
- Entry, exit, sizing, and risk controls must be **implementable**.
  If they aren't, this hypothesis isn't ready — go back to
  `distill-idea.md` (and possibly to `review-source-dossier.md` to
  reject upstream).
- Do not invent data we don't have. If the memo flagged this as a
  problem, it should still be a problem here.
- The `Parent source artifacts` block must point to `dossier.md`,
  `memo.md`, and the `raw_capture_path`.

## Finalize

When the spec is filled:

```bash
.venv/bin/python scripts/write_hypothesis_spec.py --slug <thesis_slug> --finalize
```

`--finalize` runs the validator and flips the ingestion row to
`SPEC_READY/PENDING`. The next cron run of `discover_strategies.py`
will promote it into the lifecycle DB at `PROPOSED`.

## Output

The path to the finalized spec, plus a short note on which acceptance
criteria the strategy is targeting. Do not transition the lifecycle
state yourself — discovery is the canonical writer.
