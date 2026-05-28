---
artifact_type: idea_memo
intake_id: 20
capture_slug: this-simple-scalping-strategy-makes-me-over-1000
thesis_name: 
thesis_slug: 
source_title: this-simple-scalping-strategy-makes-me-over-1000
source_url: https://www.youtube.com/watch?v=xTTDH5iRhJc
raw_capture_path: research/captures/raw/youtube/telegram-auto-ingest/2026-05-05/3cfdc5e491b392e0.json
upstream_artifact: research/hypotheses/this-simple-scalping-strategy-makes-me-over-1000/dossier.md
recommended_next_action: reject
---

# Idea memo — Break & Bounce intraday scalping

Upstream dossier: `research/hypotheses/this-simple-scalping-strategy-makes-me-over-1000/dossier.md`

## Claimed edge
A three-timeframe (Daily / 15m / 5m) intraday reversal scalp on US
futures and equities: box the prior-day daily high/low, wait for a
15-minute "Break & Bounce" of that range during the first 2.5 hours of
the cash session, and enter on a 5-minute hammer/inverted-hammer/
engulfing candlestick. Targets are mechanical R-multiples.

## Polymarket fit
Effectively zero. The strategy depends on:
- continuous intraday price ticks (Polymarket markets are sparse, often
  no continuous fair value to scalp),
- a known prior-day high/low (Polymarket binaries do not have analogous
  range mechanics — outcome prices are bounded `[0, 1]` and mean-revert
  to underlying probability, not to a price-range memory),
- short holding periods (minutes-to-hours) with multiple R per day —
  Polymarket binaries usually resolve over days/weeks, and intraday
  noise rarely produces tradeable reversal patterns.

## Polymarket failure modes
- Candlestick patterns are an artifact of continuous equity microstructure
  and don't translate to a binary market.
- Tight stop / R-multiple framing assumes price-as-continuous-variable;
  binary outcome prices clip at 0 and 1.
- The strategy's PnL example is from US futures with deep books; even
  active Polymarket markets carry thin orderbooks and large spread,
  which would dominate the claimed edge.
- The video is heavily marketing-driven (linked paid course); the
  claimed live results are anecdotal.

## Required observables
For a true port: previous-day H/L, 15m breakout, 5m candlestick
sequence, ATR-like stop reference. We don't reliably have any of these
at the Polymarket-binary granularity — orderbook depth is typically
top-of-book only, and tick frequency is irregular.

## Execution assumptions
Source assumes equities/futures execution: market or stop orders, low
latency, low slippage, sub-second fills. Polymarket order execution is
CLOB-based with much larger spreads and unpredictable fill latency.
None of these assumptions hold.

## Source-to-binary mapping
Cannot be constructed. The strategy's primitives (price range, OHLC
candle, candlestick pattern) presume a continuous-price asset; binary
outcomes do not produce coherent candlestick patterns.

## Fast reject reasons
- No continuous-price analogue on Polymarket binaries.
- Candlestick triggers are absent / undefined in our data.
- Holding-period horizon mismatch (intraday vs days-to-weeks).
- Source is marketing-grade content with a paid course tie-in.

## Recommended disposition
**reject** — there is no honest translation of this scalp into a
Polymarket binary-market strategy. Mark the ingestion row
`REJECTED_SOURCE/DONE` with reason "intraday equity/futures scalp; no
Polymarket binary mapping".
