# Gambit Autonomy Backlog

Last updated: 2026-06-05 02:20 PDT

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
- [ ] Verify the intended Polymarket wallet mode (EOA vs proxy/deposit) and apply the matching `POLY_FUNDER` / `POLY_SIGNATURE_TYPE`; current equity telemetry fails authenticated CLOB checks with HTTP 401 even though L1 `derive-api-key` still works.
- [ ] Decide whether the paper fallback source is acceptable for unattended paper operation or whether PAPER should pause until real venue equity is observable again.
- [ ] Start the next research cycle with system health and queue review.

## Blockers / approval gates
- New live deployments require explicit user approval.
- Any paid-data or paid-tool route is disallowed.

## Latest heartbeat findings
- Risk state still normal: no `data/.kill_switch`; no active paper/live/ingestion processes found.
- Lifecycle queue shape is unchanged: `tick-mean-revert` is the only PAPER slug; several Polymarket slugs remain PROPOSED; `hl-donchian` remains correctly REJECTED after the earlier cron fix.
- Budget ledger for today still shows zero counted starts/backtests/tokens.
- `scripts/portfolio_status.py --refresh --no-event` still resolves `tick-mean-revert` to a 300 USDC cap only via `venue_equity_source="paper-fallback"`; real Polymarket equity telemetry remains unavailable.
- Root cause is now sharper: `data-api /value` returns 0.0 for both the signer and discovered proxy wallet, and authenticated CLOB calls (`/balance-allowance`, `/data/orders`) return HTTP 401.
- Verified this is not a dead L1 signer path: direct `GET /auth/derive-api-key` with L1 headers still succeeds, so the failure is more likely wrong/missing `POLY_FUNDER` / `POLY_SIGNATURE_TYPE` (wallet mode mismatch) than broken network reachability.
- Hardened `scripts/check_env.py` so future health checks detect this explicitly via a new `Polymarket CLOB auth` check and surface optional `POLY_FUNDER` / `POLY_SIGNATURE_TYPE` env slots.
- Added targeted regression coverage in `tests/test_check_env.py`; `.venv/bin/python -m pytest tests/test_check_env.py -q` passes.
- Current `scripts/check_env.py --verbose` result is now intentionally red on this machine: 28/29 checks passed, with the sole failure `Polymarket CLOB auth = unauthorized`.

## Notes for future runs
- Semi-daily briefings should cover the prior 12 hours and the intended next 12 hours.
- The evening cycle should include reflection and a concrete self-improvement action where justified.
- The heartbeat cadence is temporarily every 15 minutes for tonight; evaluate token cost versus value before deciding whether to return to hourly.
- Legacy Trading-Lab crons are paused so the autonomy loop is the primary control plane for now.
- Cron fix and its regression test were committed and pushed on `main` as `4bbb029` (`fix(cron): apply hl optimize lifecycle transitions`).
- Unrelated dirty file still present: `research/paper_reports/tick-mean-revert_20260604.md`.
- Next best action: verify the account's intended Polymarket wallet mode and set the matching `POLY_FUNDER` / `POLY_SIGNATURE_TYPE` in `.env` via an operator-approved secrets update; until that is fixed, paper caps depend on `paper-fallback` and are not grounded in authenticated venue equity.
