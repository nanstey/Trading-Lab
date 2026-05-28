---
artifact_type: idea_memo
intake_id: 19
capture_slug: the-privacy-subsidy-in-continuous-time-kyle-cumu
thesis_name: 
thesis_slug: 
source_title: The Privacy Subsidy in Continuous-Time Kyle
source_url: http://arxiv.org/abs/2605.25631v1
raw_capture_path: research/captures/raw/arxiv/q-fin.TR/2026-05-25/eba4172a4cb21a89.json
upstream_artifact: research/hypotheses/the-privacy-subsidy-in-continuous-time-kyle-cumu/dossier.md
recommended_next_action: reject
---

# Idea memo — Privacy Subsidy in Continuous-Time Kyle

Upstream dossier: `research/hypotheses/the-privacy-subsidy-in-continuous-time-kyle-cumu/dossier.md`

## Claimed edge
Theoretical extension of Kyle's single-period model to continuous-time
with a noise-perturbed order-flow observation channel. The paper
derives closed-form equilibrium price-impact coefficient and cumulative
liquidity-pool transfer as a function of privacy-noise intensity. Also
shows a structural duality between this "privacy subsidy" and Loss-
Versus-Rebalancing (LVR) in AMMs.

## Polymarket fit
The paper does not propose a tradeable strategy. It is a welfare /
pricing result for committed Bayesian AMMs in a Kyle-style model.
Polymarket runs a CLOB, not a committed AMM with a Brownian privacy
channel — the model's primitives (continuous order flow with additive
Gaussian noise, single risky asset with normal liquidation value) are
not Polymarket primitives.

## Polymarket failure modes
- Polymarket binaries have bounded payoffs `{0, 1}`; the Kyle model
  assumes a normally-distributed liquidation value, so the equilibrium
  doesn't transfer.
- There is no "committed AMM with privacy channel" on Polymarket;
  applying the result would require us to set up a venue, not trade on
  one.
- LVR concepts apply to AMMs, not orderbooks.

## Required observables
None of the model's primitives are observable on Polymarket. To compute
λ we would need privacy-channel diffusion intensity σ_ε and fundamental
value diffusion σ_v — neither is meaningful on a binary CLOB.

## Execution assumptions
N/A — the paper does not propose execution rules; it derives welfare
and break-even-fee bounds.

## Source-to-binary mapping
Cannot be constructed. The paper is a microstructure-theory result with
no implementation surface in CLOB-based binary markets.

## Fast reject reasons
- Paper proposes no trading rule.
- Model primitives (Brownian order flow, normal liquidation value,
  committed AMM) are not Polymarket primitives.
- Result is about AMM-protocol welfare, not trader PnL.

## Recommended disposition
**reject** — theoretical AMM-microstructure result with no executable
strategy on Polymarket. Mark the ingestion row
`REJECTED_SOURCE/DONE` with reason "theoretical Kyle/LVR welfare paper;
no executable strategy".
