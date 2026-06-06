# AlphaInsider Crypto Strategy Intake and Paper-Trade Clone Plan

> For Hermes: execute this iteratively in verified atomic layers; commit and push after each completed repo change. Use the Hermes kanban board `trading-lab` as the durable task ledger.

Goal: turn AlphaInsider’s public crypto strategy leaderboard into a disciplined intake funnel, select 3-5 strong candidates, clone them into Trading-Lab research artifacts, and drive the finalists toward our own paper trading.

Architecture:
- Treat AlphaInsider and TradingView as untrusted source material: reported returns and screenshots are lead signals, not evidence.
- Separate the work into four layers: source capture, shortlist scoring, clone-readiness due diligence, and Trading-Lab implementation/paper validation.
- Use Hyperliquid paper as the default destination for cloned crypto strategies unless analysis shows a candidate maps better to an existing venue path. Do not let venue choice remain implicit.

Tech stack:
- Python 3.11/3.12 in repo venv
- Trading-Lab research funnel under `research/`
- Hermes kanban board `trading-lab`
- AlphaInsider public strategy pages + TradingView script pages + Pine source links
- Hyperliquid paper runtime for eventual paper deployment

Human intervention / approval gates:
1. Approve the final 3-5 strategy shortlist before implementation starts.
2. Approve the default clone venue / market universe if the evidence points away from Hyperliquid paper.
3. Approve transition from dossier-only research into actual strategy implementation.
4. Approve promotion from backtest/smoke evidence into PAPER_READY / PAPER.

Known source evidence gathered so far:
- AlphaInsider exposes public crypto strategy listings with reported period returns and TradingView links.
- Example candidate pages observed from the current `year` crypto view include:
  - `Noro's TrendMA Strategy` — 93.17% year, 191% 5Y, 62 subscribers
  - `Real Turtle` — 79% year, 94.74% 5Y, 0 subscribers
  - `Strategic Multi-Step Supertrend` — 70.33% year, -24.97% 5Y, 4 subscribers
  - `Ichimoku Kinko Hyo: ETH 3h Strategy` — 59.89% year, 138% 5Y, 197 subscribers
  - `RSI2 Scapling` — 52.38% year, 67.86% 5Y, 8 subscribers
  - `MA Cross` — 46.61% year, 138% 5Y, 5 subscribers
- TradingView pages expose at least some strategy rules and open-source status, but direct Pine access may require brittle handling or fallbacks.

Selection principles:
1. Prefer mechanistic clarity over raw return.
2. Prefer open-source Pine scripts over opaque scripts.
3. Prefer strategies with stable multi-horizon returns over one-window spikes.
4. Prefer candidates portable to our current venue/runtime stack.
5. Prefer strategies whose execution assumptions can be falsified with our data.
6. Deprioritize generic labels (`sol`, `ltc`, etc.) unless the underlying logic is recoverable.

## Phase 1 — Build the candidate universe

Objective: create a durable local catalog of AlphaInsider crypto strategies with provenance.

Files / paths:
- Create: `research/captures/alphainsider/2026-06-05-crypto-search.md`
- Create: `research/captures/alphainsider/2026-06-05-crypto-strategy-catalog.csv`
- Create: `research/captures/alphainsider/2026-06-05-crypto-strategy-catalog-notes.md`

Outputs required:
- one row per visible AlphaInsider crypto strategy
- AlphaInsider URL
- strategy name
- author / portfolio
- subscriber count
- day/week/month/year/5Y returns where visible
- start date / recency window where visible
- TradingView link if present
- notes on missing data / oddities

Verification:
- the catalog exists on disk
- every shortlisted row has a source URL
- at least one sample row is manually spot-checked against the live page

## Phase 2 — Score and triage the universe

Objective: reduce the full catalog to a serious review pool of roughly 8-12 candidates.

Files / paths:
- Create: `research/captures/alphainsider/2026-06-05-crypto-scorecard.md`
- Create: `research/captures/alphainsider/2026-06-05-crypto-shortlist.csv`

Scoring dimensions:
- performance stability across month/year/5Y
- recency / survivorship concerns
- subscriber count as weak social proof only
- rule transparency
- open-source Pine availability
- venue portability to our stack
- likely execution realism
- novelty vs strategies already in `research/hypotheses/`

Exit condition:
- top review pool narrowed to 8-12 candidates
- clear explanation for why each candidate survived triage
- clear explanation for why obvious weak candidates were cut

## Phase 3 — Recover the actual strategy logic

Objective: turn leaderboard rows into executable strategy descriptions.

Files / paths:
- Create: `research/captures/alphainsider/tradingview/<slug>.md` for each reviewed candidate
- Create when available: `research/captures/alphainsider/pine/<slug>.pine`
- Create: `research/captures/alphainsider/2026-06-05-source-recovery-log.md`

For each candidate, capture:
- AlphaInsider performance page
- TradingView script page
- Pine source if open-source and retrievable
- explicit entry / exit / filter rules
- timeframe and asset assumptions
- required indicators and parameters
- unknowns / ambiguities / hidden assumptions

Exit condition:
- each serious candidate has a reconstructed rule sheet
- each candidate is marked one of:
  - cloneable now
  - cloneable with moderate inference risk
  - not cloneable due to missing logic

## Phase 4 — Map candidates onto Trading-Lab reality

Objective: determine whether each candidate can be tested honestly in our system.

Questions to answer per candidate:
- Which venue should host the clone first? Default assumption: Hyperliquid paper.
- Which market set is appropriate?
- What bar frequency / signal cadence is required?
- Does our current data/runtime support this honestly?
- What execution/fill assumptions would make a naive clone misleading?
- Can multiple candidates share a common indicator / signal scaffold?

Files / paths:
- Create: `research/captures/alphainsider/2026-06-05-clone-feasibility.md`

Exit condition:
- each candidate has an explicit venue-fit verdict
- implementation blockers are surfaced before coding starts
- the eventual clone destination is no longer implicit

## Phase 5 — Choose the final 3-5 clones

Objective: lock the first implementation cohort.

Selection rule:
- choose 3-5 candidates balancing:
  - strongest reported performance quality
  - strongest rule transparency
  - strongest fit to our runtime
  - diversity of strategy family (avoid five near-duplicates)

Required output:
- final ranked shortlist with “why this, why now”
- reserve list of near-misses
- explicit reasons for excluded high-return but low-trust candidates

Human gate:
- operator approval required before implementation begins

## Phase 6 — Convert finalists into Trading-Lab hypotheses

Objective: register the chosen clones as first-class research objects.

Files / paths:
- Create: `research/hypotheses/<slug>/dossier.md` for each finalist
- Register via: `.venv/bin/python scripts/propose_hypothesis.py --file research/hypotheses/<slug>/dossier.md`
- Verify via: `.venv/bin/python scripts/research_cli.py show --slug <slug>`

Each dossier should include:
- source URLs
- claimed edge mechanism
- reconstructed rules
- venue / market fit
- implementation requirements
- parameter surface
- failure modes
- acceptance criteria for clone fidelity and paper worthiness

Exit condition:
- all finalists registered in `research/experiments.db`
- dossier quality is strong enough to guide implementation without guesswork

## Phase 7 — Build the clone scaffold

Objective: create the minimum reusable infrastructure to port multiple TradingView-style strategies efficiently.

Likely repo touchpoints:
- `src/trading_lab/strategies/`
- `scripts/`
- `tests/`

Expected work:
- decide whether to create one shared indicator/signal helper layer for TradingView-style clones
- add parity tests where Pine logic can be mirrored deterministically
- keep each actual strategy as its own new slug / strategy module

Important invariant:
- do not edit a registered strategy in place; fork to a new slug when needed

Exit condition:
- reusable scaffold exists for the finalist cohort
- first clone can be implemented without ad hoc architecture churn

## Phase 8 — Implement and validate the finalists

Objective: port each approved finalist and prove that the clone is mechanically credible.

Per finalist:
1. create strategy module + config
2. add unit/smoke tests
3. run smoke test
4. run venue-appropriate backtest or bounded validation
5. record fidelity gaps vs source logic
6. decide whether the clone deserves paper promotion

Required verification:
- `.venv/bin/python scripts/smoke_test_strategy.py --slug <slug>`
- relevant targeted pytest
- venue-appropriate backtest / evaluation script

## Phase 9 — Paper-trade the winners

Objective: start our own paper trading only after clone fidelity and venue fit are good enough.

Per winner:
- confirm kill switch clear and environment health
- verify no duplicate runner holds the slot
- respect lifecycle gates into PAPER_READY / PAPER
- start bounded paper run
- collect paper evidence and operator-facing summary

Success condition for the overall initiative:
- one durable AlphaInsider strategy catalog
- one ranked shortlist with operator-approved finalists
- 3-5 registered clone hypotheses
- at least 1-2 finalists implemented and validated well enough to justify bounded paper runs
- paper-trading decisions grounded in our own evidence, not AlphaInsider marketing

Initial recommended review pool from current observed data:
- Noro's TrendMA Strategy
- Real Turtle
- Strategic Multi-Step Supertrend
- Ichimoku Kinko Hyo: ETH 3h Strategy
- RSI2 Scapling
- MA Cross

Deprioritize initially unless the source logic becomes much clearer:
- `sol`
- `ltc`
- `Harshtrades`
- any script with strong returns but weak rule transparency or unrecoverable Pine
