---
artifact_type: idea_memo
intake_id: 7
capture_slug: i-backtested-rsi-trading-strategy-for-6-years-60
thesis_name: 
thesis_slug: 
source_title: i-backtested-rsi-trading-strategy-for-6-years-60
source_url: https://www.youtube.com/watch?v=lpKpwxLBVm8
raw_capture_path: research/captures/raw/youtube/telegram-auto-ingest/2026-05-22/6b03fe8618517e68.json
upstream_artifact: research/hypotheses/i-backtested-rsi-trading-strategy-for-6-years-60/dossier.md
recommended_next_action: reject
---

# Idea memo — Six-year RSI backtest

Upstream dossier: `research/hypotheses/i-backtested-rsi-trading-strategy-for-6-years-60/dossier.md`

## Claimed edge
Long-only RSI(2) on US equity ETFs: buy when RSI(2) < 10, sell on a
higher close. Creator reports ~60% win-rate over a six-year period with
no walk-forward / out-of-sample split.

## Polymarket fit
None. RSI is a smoothed momentum oscillator on continuous price; a
Polymarket binary doesn't have an equity-like momentum series — its
"price" is the market's estimate of a resolution probability, which
moves on information shocks, not on the random-walk-plus-noise
dynamics that make RSI(2) reversion work on indices.

## Polymarket failure modes
- RSI(2) on a probability series captures only "recent updates were
  downward"; the corresponding reversion mechanism (institutional
  flow, dealer hedging) doesn't exist on Polymarket.
- The cited backtest is in-sample and over the discovery period.
- Polymarket binaries have terminal absorption at 0 or 1 near
  resolution — RSI(2) would generate false triggers as a market
  approaches certainty.

## Required observables
RSI(2) requires a clean close-price series sampled at consistent
intervals. Polymarket trade data is irregular; an "RSI on YES mid-price"
is constructible but unmotivated.

## Execution assumptions
Source assumes index-ETF liquidity (sub-cent spreads, instant fills).
Polymarket binaries have multi-cent spreads and unpredictable fill
latency that would absorb any reversion edge.

## Source-to-binary mapping
Mechanical mapping is possible (RSI on YES mid). No causal mechanism
justifies expecting reversion in that series, so the mapping is
syntactic, not semantic.

## Fast reject reasons
- No causal reversion mechanism on Polymarket probability prices.
- Spread / execution cost would exceed any residual edge.
- Backtest evidence is in-sample on equities, not Polymarket.
- RSI(2) has been widely known on equities since Connors (2009).

## Recommended disposition
**reject** — no causal basis on Polymarket binaries. Mark the ingestion
row `REJECTED_SOURCE/DONE` with reason "in-sample equity RSI backtest;
no Polymarket mechanism".
