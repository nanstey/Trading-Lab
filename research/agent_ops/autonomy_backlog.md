# Gambit Autonomy Backlog

Last updated: 2026-06-05 01:34 PDT

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
- [ ] Triage why `portfolio_status.py` reports `venue_equity_usdc=0.0` / no telemetry source for the active PAPER allocation.
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

## Notes for future runs
- Semi-daily briefings should cover the prior 12 hours and the intended next 12 hours.
- The evening cycle should include reflection and a concrete self-improvement action where justified.
- The heartbeat cadence is temporarily every 15 minutes for tonight; evaluate token cost versus value before deciding whether to return to hourly.
- Legacy Trading-Lab crons are paused so the autonomy loop is the primary control plane for now.
- Cron fix and its regression test were committed and pushed on `main` as `4bbb029` (`fix(cron): apply hl optimize lifecycle transitions`).
- Unrelated dirty file still present: `research/paper_reports/tick-mean-revert_20260604.md`.
