---
artifact_type: idea_memo
intake_id: 5
capture_slug: hidden-markov-models-for-quant-finance
thesis_name: 
thesis_slug: 
source_title: Hidden Markov Models for Quant Finance
source_url: https://www.youtube.com/watch?v=g_AVOZ8MwsM
raw_capture_path: research/captures/raw/youtube/manual-link-drop/2025-09-30/290c39b3b671e358.json
upstream_artifact: research/hypotheses/hidden-markov-models-for-quant-finance/dossier.md
recommended_next_action: shelve
---

# Idea memo — HMM regime filter (building block, not strategy)

Upstream dossier: `research/hypotheses/hidden-markov-models-for-quant-finance/dossier.md`

## Claimed edge
The video is a tutorial introducing two-state Gaussian HMMs as a regime
detector for equity returns. The "edge" framed in the source is
descriptive — HMM-inferred regimes correlate with realised volatility
clusters — not a complete trading strategy. The presenter does not
demonstrate a tradeable rule end-to-end.

## Polymarket fit
Partial / indirect. A regime filter built from realised volatility in
Polymarket aggregates could plausibly gate other Polymarket-binary
strategies (e.g. "only run the spread-fade when market-wide volume
regime is calm"). It is not a strategy by itself.

## Polymarket failure modes
- HMM training on Polymarket binaries needs a homogenous observable.
  Outcome-price returns differ wildly across categories (politics,
  sports, weather) — a single global HMM is meaningless; a per-category
  HMM needs more data than most categories carry.
- Two-state Gaussian HMMs assume Gaussian innovations, which is
  unrealistic for binary-market price changes (heavy-tailed, bounded).
- A regime label is not a P&L signal — turning a regime into a position
  rule still requires a separate strategy to gate.
- Walk-forward HMM re-training is non-trivial: state labels can flip
  between training windows, making operational use brittle.

## Required observables
- Per-binary mid-price return time series, sampled at consistent
  intervals.
- Or, market-wide aggregate (e.g. mean abs return across actively-traded
  Polymarket categories).
- HMM library (e.g. `hmmlearn`) — not currently in the project deps.

## Execution assumptions
N/A — HMM is a filter, not an execution model. Whatever strategy it
gates would carry its own execution rules.

## Source-to-binary mapping
HMM regime → binary "high/low realised-vol regime". Use that label as a
gate flag in an existing strategy's entry rule. The mapping is clean
*if* the underlying strategy already exists.

## Fast reject reasons
- No standalone strategy in the source.
- Adding HMM infrastructure for a hypothetical gate is unjustified
  before we have a host strategy that demonstrably needs it.
- Two-state Gaussian HMMs are a poor fit for binary-market price
  changes; a more honest base model would need to be built.

## Recommended disposition
**shelve** — track the HMM regime-filter idea as a *future building
block* once we have a base Polymarket strategy that would meaningfully
benefit from a regime gate. Do not promote to spec on its own.
Mark the ingestion row `SHELVED_SOURCE/DONE` with reason
"useful primitive, but not a standalone strategy; revisit once a host
strategy needs a regime filter".
