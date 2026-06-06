# Gambit Autonomy Backlog

Last updated: 2026-06-05 23:12 PDT

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
2. Review lifecycle queues, experiment DB, paper/live state, recent cron outputs, and the Hermes kanban board.
3. Pick the highest expected-value safe next action.
4. Claim the corresponding kanban task, execute with durable artifacts and verification, and update its status.
5. Update this backlog with high-level findings, blockers, decisions, and next actions; keep task-level state in kanban.
6. Deliver scheduled briefings at 09:00 and 21:00 local time.

## Current priorities
- Repair degraded venue-equity telemetry before trusting pct-of-equity paper caps.
- Reduce autonomy control-plane spend before expanding research scope; the latest 1-day reading shows 17.50M cron tokens across 21 cron sessions and 63.18M total tokens across all surfaces, with 45.68M of the total coming from Telegram rather than cron. Cost control is still required, but attribution needs to stay precise.
- Start the next research cycle from the highest-signal PROPOSED Polymarket slug only after the telemetry risk is either fixed or explicitly accepted and the control loop is leaner.
- Keep paused legacy Trading-Lab crons paused until the new loop proves it covers the needed control-plane duties.

## Open TODOs
- [x] Audit current cron/research/paper coverage against the autonomy mandate.
- [x] Install hourly heartbeat cron.
- [x] Correct the heartbeat cron schedule mismatch (it regressed back to `every 15m` by 2026-06-05 22:44 PDT; `gambit-autonomy-heartbeat` was reset to `every 1h` and its prompt header now says `hourly autonomy heartbeat`).
- [x] Install 09:00 and 21:00 briefing crons.
- [ ] Define how token-usage and work-in-progress are summarized durably.
- [ ] Re-measure `hermes insights --days 1` after ~24h of the truly-hourly heartbeat plus trimmed briefing prompts; if cron spend is still too high, decide between further no-agent/scripted conversion and additional prompt cuts.
- [ ] Separate cron-spend control from broader Hermes usage: the latest 1-day reading shows cron at 17.50M tokens but Telegram at 45.68M, so future cost triage should not misattribute total token pressure to the autonomy crons alone.
- [ ] Identify what is rewriting `gambit-autonomy-heartbeat` back to `every 15m`; the schedule regressed again between the prior 22:44 PDT correction and this 23:12 PDT run, so a second manual edit is not a durable fix.
- [x] Investigate the failed optimize-queue cron run and remediate if needed.
- [ ] Verify the intended Polymarket wallet mode (EOA vs proxy/deposit), set the matching `POLY_FUNDER` / `POLY_SIGNATURE_TYPE`, and refresh L2 credentials if needed; current equity telemetry fails authenticated CLOB checks with HTTP 401 for both the signer and discovered proxy wallet even though L1 `derive-api-key` still works.
- [ ] Decide whether the paper fallback source is acceptable for unattended paper operation or whether PAPER should pause until real venue equity is observable again.
- [ ] Start the next research cycle with system health and queue review.

## Blockers / approval gates
- New live deployments require explicit user approval.
- Any paid-data or paid-tool route is disallowed.

## Latest heartbeat findings
- 23:12 PDT risk remains constrained: `data/.kill_switch` is still absent and there are no live `paper_run_v2.py`, `live_run.py`, or `run_ingestion.py` processes.
- The highest-value issue was another autonomy-heartbeat schedule regression: `hermes cron list` and `~/.hermes/profiles/gambit/cron/jobs.json` both showed `gambit-autonomy-heartbeat` back at `every 15m`, and the stored prompt header had also drifted back to `15-minute autonomy heartbeat`.
- Highest-value safe action this run was to repair that drift again with `hermes cron edit b301392adfc4 --schedule 'every 1h' --prompt ...`, restoring both the interval and the prompt header to hourly.
- Durable verification passed: `hermes cron list` now shows the heartbeat at `every 60m` with next run `2026-06-06T00:12:24.070763-07:00`, and `~/.hermes/profiles/gambit/cron/jobs.json` now records `minutes: 60`, `schedule_display: every 60m`, and the first prompt line `You are Gambit running the hourly autonomy heartbeat...`.
- Lifecycle queue shape is unchanged: `tick-mean-revert` remains the only PAPER slug; `hl-donchian-alts-trial` and `hl-smoke` remain `PAPER_READY`; there are still no LIVE slugs; leading Polymarket `PROPOSED` slugs remain `polymarket-ladder-maker`, `polymarket-sticky-btc-leadlag`, `polymarket-btc-5m-price-field`, and `polymarket-endcycle-sniper`.
- Budget ledger for today remains idle: `llm_tokens=0`, `backtests=0`, `paper_starts=0`, `live_starts=0`.
- Paper operations are mechanically quiet but still economically degraded: `scripts/portfolio_status.py --refresh --no-event` again failed both data-api and CLOB equity refreshes, then fell back to `venue_equity_source="paper-fallback"`, leaving `tick-mean-revert` capped off a synthetic 1000 USDC venue equity and an effective 300 USDC per-slug cap.
- Next best action is to root-cause the recurring heartbeat schedule rewrite before trusting the hourly cadence or using it for a clean 24h spend sample; the Polymarket authenticated-equity telemetry failure remains the main paper-ops blocker behind that.

## Notes for future runs
- Semi-daily briefings should cover the prior 12 hours and the intended next 12 hours.
- The evening cycle should include reflection and a concrete self-improvement action where justified.
- Treat heartbeat cadence as a live control-plane invariant, not a one-time fix: verify the actual Hermes scheduler state instead of trusting backlog prose.
- Legacy Trading-Lab crons remain paused so the autonomy loop is the primary control plane for now.
- Cron fix and its regression test were committed and pushed on `main` as `4bbb029` (`fix(cron): apply hl optimize lifecycle transitions`).
- Unrelated dirty file still present: `research/paper_reports/tick-mean-revert_20260604.md`.
- The enhanced Polymarket auth-diagnostic patch is now committed and pushed on `main` as `1109041` (`fix(env): probe polymarket proxy wallet in auth check`).
- Next best action: re-measure `hermes insights --days 1` after roughly 24 hours of the re-corrected hourly heartbeat and trimmed briefing prompts; if cron spend is still unattractive, trim or scriptify further only after keeping the auth-telemetry blocker front and center.
