---
slug: hl-trend-following-long-only
venue: hyperliquid
source: alphainsider
source_url: https://alphainsider.com/strategy/Q2Pq6Sg_MKwDlrd0FZLx5?timeframe=year
created: 2026-06-07
parent_slug: null
state: PROPOSED
bar_interval: 1d
funding_aware: true
market_criteria:
  venue: hyperliquid
  symbols: [BTC]
  preferred_timeframe: 1d
  candidate_timeframes: [1d]
  requires_perps: true
  requires_timeframe_clarification: false
strategy_module: trading_lab.strategies.hl_trend_following_long_only
strategy_class: HLTrendFollowingLongOnlyStrategy
strategy_config_class: HLTrendFollowingLongOnlyConfig
strategy_params:
  lookback_length: 200
  smoother_length: 3
  atr_length: 10
  atr_multiplier: 0.5
  long_only: true
---

# hl-trend-following-long-only

## Hypothesis
Clone the AlphaInsider / TradingView Trend Following Long Only Strategy as a new Hyperliquid-perp hypothesis. The recovered operative logic is a long-only smoothed price-channel / ATR trend regime: `lookback_length=200`, `smoother_length=3`, `atr_length=10`, `atr_multiplier=0.5`, smoothed high/low channel bands via `ema(lowest())` and `ema(highest())`, long entry on `crossover(trend, 0.0)`, and exit on `crossunder(trend, 0.0)`. The default source context is now tightened to BTC on `1d` bars rather than a generic multi-asset / unknown-timeframe family reconstruction.

## Source evidence
- AlphaInsider strategy id: `Q2Pq6Sg_MKwDlrd0FZLx5`
- AlphaInsider page: `https://alphainsider.com/strategy/Q2Pq6Sg_MKwDlrd0FZLx5?timeframe=year`
- Linked TradingView script: reachable public open-source page, direct extraction blocked by reCAPTCHA
- Secondary recovery source: Scribd-hosted Pine v4 excerpt exposing the operative logic
- Timeframe/scope clarification artifact: `research/captures/alphainsider/2026-06-07-hl-trend-following-long-only-timeframe-scope-v1.md`
- Recovery artifacts:
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v2.md`
  - `research/captures/alphainsider/2026-06-06-crypto-clone-feasibility-v1.md`

## Recovered rule sheet
- Strategy family: long-only channel / ATR trend-following system.
- Recovered parameters from the secondary-source excerpt:
  - `lookback_length = 200`
  - `smoother_length = 3`
  - `atr_length = 10`
  - `atr_multiplier = 0.5`
  - smoothed high/low channel via `ema(lowest())` and `ema(highest())`
  - long entry on `crossover(trend, 0.0)`
  - exit on `crossunder(trend, 0.0)`
- Clarified default source context from the live TradingView page:
  - timeframe: `1d`
  - instrument context: `BraveNewCoin Liquid Index for Bitcoin`
  - honest first-pass Trading-Lab scope: BTC-first on daily bars
- Remaining ambiguity: direct TradingView source extraction is still blocked by reCAPTCHA, so broader multi-asset intent is not proven from first-party source code.

## Venue fit
- Primary venue: Hyperliquid perps.
- First-pass candidate instruments: `BTC-PERP`, `ETH-PERP`.
- Why not Polymarket: this is a directional crypto trend strategy, not a binary-market thesis.

## Implementation requirements
- Implement the first pass as a BTC-only `1d` Hyperliquid clone unless new first-party source evidence expands scope.
- New HL-native strategy slug/module; do not modify an existing registered strategy in place.
- Keep the remaining provenance caveat explicit: rule logic is strong, but direct TradingView source extraction is still blocked.
- Include funding and fee effects in evaluation.

## Main risks
- Provenance still depends partly on a secondary-source excerpt rather than direct TradingView source extraction.
- If the author intended a broader asset universe than the visible BTC default chart, a BTC-only first pass may still understate scope.
- A long-only strategy may be sensitive to market selection and sample window.

## Acceptance criteria
- Folder-style hypothesis is registered and verified in the lifecycle DB.
- The source-native timeframe / market-scope ambiguity is tightened in a durable artifact before any parity claim.
- A later HL-native implementation and bounded backtest produce durable artifacts with assumptions stated explicitly.
- Research verdict distinguishes clone fidelity from raw backtest output.
