# Trading-Lab autonomy usage + WIP summary

Last refreshed: 2026-06-06 01:57:41 PDT

## Purpose
This file is the durable reference for how the Trading-Lab autonomy loop summarizes recent Hermes usage and current board work-in-progress during heartbeat reviews.

## Evidence sources
- `hermes insights --days 1`
- `hermes kanban --board trading-lab stats`
- `hermes kanban --board trading-lab list --json`
- read-only SQLite query against `~/.hermes/profiles/gambit/state.db` grouping `source='cron'` sessions by the embedded job id in `sessions.id` (`cron_<jobid>_<timestamp>`)

## Current 24h usage snapshot
Source: `hermes insights --days 1` run at 2026-06-06 01:47 PDT.

- total tokens: 38,775,572
- sessions: 43
- tool calls: 1,255
- platform mix:
  - cron: 21,866,178 tokens across 36 sessions
  - telegram: 12,280,566 tokens across 2 sessions
  - cli: 4,628,828 tokens across 5 sessions

## Narrower autonomy-cron attribution
Source: read-only `sqlite3` query against `~/.hermes/profiles/gambit/state.db` at 2026-06-06 01:56 PDT, grouping the last 24h of `source='cron'` sessions by the job id embedded in `sessions.id`.

- `b301392adfc4` (`gambit-autonomy-heartbeat`): 32 sessions, 20,188,051 session-store tokens, 775 tool calls
- `dae7b8a75290` (`gambit-autonomy-planner`): 1 session, 796,840 session-store tokens, 36 tool calls
- `45130b784ed8` (`gambit-autonomy-briefing-pm`): 2 sessions, 718,795 session-store tokens, 50 tool calls
- `4ab09e0b9304` (`gambit-autonomy-briefing-am`): 1 session, 555,301 session-store tokens, 42 tool calls
- grouped session-store total: 22,258,987 tokens across the four active Trading-Lab autonomy jobs in the last 24h

Exact query pattern used:

```sql
SELECT substr(id, 6, 12) AS job_id,
       COUNT(*) AS sessions,
       SUM(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)+COALESCE(cache_read_tokens,0)+COALESCE(cache_write_tokens,0)+COALESCE(reasoning_tokens,0)) AS total_tokens,
       SUM(COALESCE(tool_call_count,0)) AS tool_calls
FROM sessions
WHERE source='cron' AND started_at >= strftime('%s','now','-1 day')
GROUP BY job_id
ORDER BY total_tokens DESC;
```

## Current board WIP snapshot
Source: `hermes kanban --board trading-lab stats` run at 2026-06-06 01:57 PDT.

- ready: 0
- running: 0
- todo: 5
- blocked: 7
- done: 11
- oldest ready task age: none

## Interpretation rules
1. Treat `cron` from `hermes insights --days 1` as the platform-level envelope for autonomy spend.
2. For Trading-Lab control-plane attribution, split that cron envelope by job id using the session-store query above before making cost judgments.
3. Do not attribute total daily Hermes usage to the Trading-Lab heartbeat alone while `telegram` or unrelated `cli` sessions are materially active.
4. Use board WIP counts to answer "how much open work is in motion"; use task comments/results for detailed progress.
5. Keep the Polymarket venue-equity telemetry blocker as the primary control-plane concern unless usage spikes enough to justify immediate cost action.

## Current caveat
This summary is now narrower than the old platform-only view, but it is still operational rather than billing-grade exact. The session-store grouped total (22,258,987) will not always match the adjacent `hermes insights` cron bucket (21,866,178) exactly because the two captures are taken at different moments and may not share identical token-accounting rules. Use the grouped query for relative per-job attribution, not invoice-grade reconciliation.
