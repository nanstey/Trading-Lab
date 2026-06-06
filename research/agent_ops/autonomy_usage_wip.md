# Trading-Lab autonomy usage + WIP summary

Last refreshed: 2026-06-06 01:38:08 PDT

## Purpose
This file is the durable reference for how the Trading-Lab autonomy loop summarizes recent Hermes usage and current board work-in-progress during heartbeat reviews.

## Evidence sources
- `hermes insights --days 1`
- `hermes kanban --board trading-lab stats`
- `hermes kanban --board trading-lab list --json`

## Current 24h usage snapshot
Source: `hermes insights --days 1` run at 2026-06-06 01:38 PDT.

- total tokens: 39,739,087
- sessions: 43
- tool calls: 1,278
- platform mix:
  - cron: 22,829,693 tokens across 36 sessions
  - telegram: 12,280,566 tokens across 2 sessions
  - cli: 4,628,828 tokens across 5 sessions

## Current board WIP snapshot
Source: `hermes kanban --board trading-lab stats` run at 2026-06-06 01:38 PDT.

- ready: 1
- running: 1
- todo: 5
- blocked: 7
- done: 9
- oldest ready task age: 9s at capture time

## Interpretation rules
1. Treat `cron` as the first-pass proxy for autonomy-loop spend.
2. Do not attribute total daily Hermes usage to the Trading-Lab heartbeat alone while `telegram` or unrelated `cli` sessions are materially active.
3. Use board WIP counts to answer "how much open work is in motion"; use task comments/results for detailed progress.
4. Keep the Polymarket venue-equity telemetry blocker as the primary control-plane concern unless usage spikes enough to justify immediate cost action.

## Current caveat
This summary is durable, but it is still a coarse attribution method. `hermes insights --days 1` separates platform buckets, not individual Trading-Lab cron jobs. A narrower autonomy-only attribution method remains open follow-up work.
