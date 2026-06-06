# Gambit Autonomy Backlog

Last updated: 2026-06-06 01:26 PDT

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
- Make Polymarket authenticated venue-equity telemetry the dominant objective for the next 4-12 hours; until it is fixed or explicitly accepted, pct-of-equity PAPER evidence remains weak.
- Use the current PAPER lane as the forcing function: `tick-mean-revert` is still the only PAPER slug, and `portfolio_status.py --refresh --no-event` still falls back to `venue_equity_source="paper-fallback"`, leaving it on a synthetic 300 USDC effective cap.
- Operational policy for now: do not start unattended PAPER sessions while venue equity remains on `paper-fallback`; resume only after real venue equity is observable again or the operator explicitly accepts the fallback.
- Treat the current 15-minute heartbeat cadence as an explicit operator override until changed again; do not frame it as drift while the live scheduler, prompt, and operator intent are aligned.
- Keep cost attribution precise but secondary for now: cron-spend re-measurement still matters, but it should not displace the telemetry blocker unless spend spikes materially.
- Defer advancing the next PROPOSED research slug until telemetry risk is reduced or explicitly accepted and the unattended-PAPER fallback policy remains unchanged.

## Open TODOs
- [x] Audit current cron/research/paper coverage against the autonomy mandate.
- [x] Install hourly heartbeat cron.
- [x] Install 09:00 and 21:00 briefing crons.
- [x] Resolve the apparent heartbeat schedule regression: the live `every 15m` cadence came from an explicit operator Telegram request at 2026-06-05 19:30 PDT, so it is not an unexplained rewrite.
- [x] Define how token-usage and work-in-progress are summarized durably (`research/agent_ops/autonomy_usage_wip.md`).
- [ ] Verify the intended Polymarket wallet mode (EOA vs proxy/deposit), set the matching `POLY_FUNDER` / `POLY_SIGNATURE_TYPE`, and refresh L2 credentials if needed; current equity telemetry still leaves authenticated refresh on `paper-fallback` despite public reachability and prior L1 success.
- [x] Decide whether the paper fallback source is acceptable for unattended paper operation or whether PAPER should pause until real venue equity is observable again.
- [ ] Re-measure `hermes insights --days 1` after a clean window of the intentional 15-minute heartbeat plus trimmed briefing prompts; keep cron-vs-Telegram attribution precise, but treat this as secondary to the telemetry blocker unless spend spikes materially.
- [ ] Separate cron-spend control from broader Hermes usage so future cost triage does not misattribute Telegram-heavy usage to the autonomy crons alone.
- [ ] Start the next research cycle only after telemetry risk is reduced or explicitly accepted and the unattended PAPER fallback policy is decided.

## Blockers / approval gates
- New live deployments require explicit user approval.
- Any paid-data or paid-tool route is disallowed.

## Latest heartbeat findings
- 01:26 PDT risk remains constrained: `data/.kill_switch` is absent.
- Fresh `.venv/bin/python scripts/check_env.py --verbose` still failed the authenticated Polymarket CLOB probe: public PM data API and Hyperliquid API are healthy, but `/balance-allowance` returned 401 for every candidate Polymarket address.
- A new bounded config check narrowed the telemetry blocker: the active Trading-Lab runtime has `POLY_FUNDER=None` and `POLY_SIGNATURE_TYPE=None`, so the repo is not currently expressing any non-EOA wallet mode.
- The auth split remains concrete: the signer derived from `POLY_PRIVATE_KEY` is `0x5195...68c0`, Gamma `public-profile` reports proxy wallet `0x5e55...40db`, and current Polymarket quickstart docs say new API users should use a deposit wallet with signature type `3` while existing EOA/proxy users must keep the matching explicit funder/signature-type pair.
- Resulting next action is now tighter: operator-side wallet-target confirmation plus refreshed L2 credentials aligned to that chosen mode remains the blocker for authenticated venue-equity telemetry.
- `hermes insights --days 1` from this run shows 39.7M total tokens over the last day, with cron at 22.8M across 36 sessions, Telegram at 12.3M across 2 sessions, and CLI at 4.6M across 5 sessions; the durable summary artifact now lives at `research/agent_ops/autonomy_usage_wip.md` and should be the default reference for future spend/WIP snapshots.
- The unattended-PAPER pause remains the correct policy while telemetry is unresolved, and board gating still keeps AlphaInsider/research advancement behind `t_01360f4c`.
- `hermes insights --days 1` from the prior run still shows heavy control-plane usage: 41.1M total tokens over the last day, with cron at 24.3M tokens across 33 sessions and Telegram at 12.2M across 3 sessions. Cost remains worth tracking, but telemetry stays the primary blocker.

## Notes for future runs
- Semi-daily briefings should cover the prior 12 hours and the intended next 12 hours.
- The evening cycle should include reflection and a concrete self-improvement action where justified.
- Treat heartbeat cadence as a live control-plane invariant, not a one-time fix: verify the actual Hermes scheduler state instead of trusting backlog prose.
- Legacy Trading-Lab crons remain paused so the autonomy loop is the primary control plane for now.
- Cron fix and its regression test were committed and pushed on `main` as `4bbb029` (`fix(cron): apply hl optimize lifecycle transitions`).
- Unrelated dirty file still present: `research/paper_reports/tick-mean-revert_20260604.md`.
- The enhanced Polymarket auth-diagnostic patch is now committed and pushed on `main` as `1109041` (`fix(env): probe polymarket proxy wallet in auth check`).
- Dominant next-window success condition: materially narrow the Polymarket wallet-mode/L2-auth uncertainty behind `paper-fallback` so authenticated venue equity becomes trustworthy again, or obtain an explicit operator waiver to run unattended PAPER on fallback telemetry.
