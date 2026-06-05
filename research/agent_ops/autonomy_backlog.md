# Gambit Autonomy Backlog

Last updated: 2026-06-05 01:57 PDT

## Mission
Develop, test, and deploy increasingly profitable trading strategies without compromising rigor, risk controls, or capital discipline.

## Hard constraints
- No live deployment of new strategies without explicit human approval.
- No bypassing lifecycle gates, kill switch, or documented risk controls.
- No falsified or weakly-supported results.
- No paid information sources.
- No outside help from people or other agents.
- Experiments must leave durable artifacts for later review.
- Token spend must stay economically sensible relative to realized edge.

## Operating loop
1. Check risk state, kill switch, and environment health.
2. Review lifecycle queues, experiment DB, paper/live state, and recent cron outputs.
3. Pick the highest expected-value safe next action.
4. Execute with durable artifacts and verification.
5. Update this backlog with findings, new TODOs, blockers, and next actions.
6. Deliver scheduled briefings at 09:00 and 21:00 local time.

## Current priorities
- Audit the current autonomous loop against the new mandate.
- Decide whether the temporary 15-minute heartbeat cadence earns its token cost.
- Repair degraded venue-equity telemetry before trusting pct-of-equity paper caps.
- Start the next research cycle from the highest-signal PROPOSED Polymarket slug.

## Open TODOs
- [ ] Audit current cron/research/paper coverage against the autonomy mandate.
- [x] Install hourly heartbeat cron.
- [x] Install 09:00 and 21:00 briefing crons.
- [ ] Define how token-usage and work-in-progress are summarized durably.
- [x] Investigate the failed optimize-queue cron run and remediate if needed.
- [ ] Triage why live Polymarket venue-equity refresh still returns non-positive balances and forces the `paper-fallback` equity source for pct-of-equity PAPER caps.
- [ ] Decide whether the paper fallback source is acceptable for unattended paper operation or whether PAPER should pause until real venue equity is observable again.
- [ ] Start the next research cycle with system health and queue review.

## Blockers / approval gates
- New live deployments require explicit user approval.
- Any paid-data or paid-tool route is disallowed.

## Latest heartbeat findings
- Risk state normal: no `data/.kill_switch`; `scripts/check_env.py` passed 26/26.
- No active paper/live/ingestion processes were running during the check.
- `tick-mean-revert` remains the only PAPER slug, but its effective cap is currently 0.0 USDC because venue-equity telemetry resolved to zero.
- Verified the Hyperliquid optimize-queue failure root cause: `hl_optimize.py` emits `decision_new_state`, but the cron wrapper previously failed state verification before applying the lifecycle transition.
- `hl-donchian` now correctly shows `OPTIMIZE -> REJECTED` via actor `agent:research-optimize-queue` at `2026-06-05T08:16:52.557529+00:00`.
- Fresh optimizer artifact exists at `research/optimizer_outputs/hl-donchian_2024-05-30_2026-06-05.json`; outcome is still economically bad despite positive pnl because min OOS trades is 0 and the decision remains REJECTED.
- Added regression coverage in `tests/test_hermes_cron_scripts.py` for the Hyperliquid auto-transition path; targeted pytest now passes.
- `scripts/portfolio_status.py --refresh --no-event` no longer leaves the PAPER slug economically disarmed: `tick-mean-revert` now resolves to a 300 USDC cap via `venue_equity_source="paper-fallback"`, but real venue-equity telemetry is still broken (`data-api` and `clob+orders` both returned non-positive balances).
- Ran a bounded `paper_run_v2.py --slug tick-mean-revert` verification: node booted cleanly, subscribed 6 Polymarket instruments, kill switch stayed clear, and the run exited normally with zero signals.
- Found and fixed a bookkeeping gap: zero-signal paper sessions returned a `log_path` but produced no file, causing `paper_summary.py` to fail with `log_not_found`.
- Patched `PaperRunnerV2` to create the session log file up front and patched `paper_summary.py` to emit a zero-signal markdown report instead of failing on an empty log; added `tests/test_paper_summary.py` and verified with targeted pytest plus a fresh bounded paper run.
- New durable artifact: `research/paper_reports/tick-mean-revert_20260605.md` (0 signals / $0.00 realised PnL, confirms bookkeeping now works for empty sessions).

## Notes for future runs
- Semi-daily briefings should cover the prior 12 hours and the intended next 12 hours.
- The evening cycle should include reflection and a concrete self-improvement action where justified.
- The heartbeat cadence is temporarily every 15 minutes for tonight; evaluate token cost versus value before deciding whether to return to hourly.
- Legacy Trading-Lab crons are paused so the autonomy loop is the primary control plane for now.
- Cron fix and its regression test were committed and pushed on `main` as `4bbb029` (`fix(cron): apply hl optimize lifecycle transitions`).
- Unrelated dirty file still present: `research/paper_reports/tick-mean-revert_20260604.md`.
- Next best action: investigate why Polymarket equity telemetry is returning non-positive balances even though paper fallback keeps the allocator alive; until that is resolved, paper ops are mechanically healthy but not grounded in real venue equity.
