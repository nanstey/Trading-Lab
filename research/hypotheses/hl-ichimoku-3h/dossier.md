---
slug: hl-ichimoku-3h
venue: hyperliquid
source: alphainsider
source_url: https://alphainsider.com/strategy/Cth7pfZgpTqh9lubqpTIw?timeframe=year
created: 2026-06-07
parent_slug: null
state: PROPOSED
bar_interval: 3h
funding_aware: true
market_criteria:
  venue: hyperliquid
  symbols: [ETH]
  preferred_timeframe: 3h
  candidate_timeframes: [1h, 3h]
  requires_perps: true
  requires_resampling_policy: true
strategy_module: trading_lab.strategies.hl_ichimoku_3h
strategy_class: HLIchimoku3HStrategy
strategy_config_class: HLIchimoku3HConfig
strategy_params:
  tenkan_length: 22
  kijun_length: 60
  senkou_b_length: 120
  displacement: 30
  rsi_threshold: 50
  volatility_gate_threshold: 0.2
---

# hl-ichimoku-3h

## Hypothesis
Clone the AlphaInsider / TradingView Ichimoku Kinko Hyo: ETH 3h Strategy as a new Hyperliquid-perp hypothesis on ETH-PERP. The recovered rule set is a 3h directional Ichimoku system with Tenkan 22, Kijun 60, Senkou Span B 120, Chikou/Senkou offsets 30, an RSI direction filter (`> 50` for longs, `< 50` for shorts), and a volatility gate (`MovingAverage > 0.2`). The honest first-pass research path uses deterministic `1h -> 3h` resampling with strict contiguous buckets before any bounded Hyperliquid backtest.

## Source evidence
- AlphaInsider strategy id: `Cth7pfZgpTqh9lubqpTIw`
- AlphaInsider page: `https://alphainsider.com/strategy/Cth7pfZgpTqh9lubqpTIw?timeframe=year`
- Linked TradingView script: reachable public open-source page, direct extraction blocked by reCAPTCHA
- Secondary recovery source: `quanttradingpro.com` mirror exposing the recovered Pine script
- Recovery artifacts:
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v2.md`
  - `research/captures/alphainsider/2026-06-06-crypto-clone-feasibility-v1.md`
  - `research/captures/alphainsider/2026-06-06-hl-ichimoku-3h-bar-policy-v1.md`

## Recovered rule sheet
- Strategy family: ETH-only Ichimoku trend strategy on native `3h` bars.
- Recovered parameters from the mirrored Pine source:
  - Tenkan: `22`
  - Kijun: `60`
  - Senkou Span B: `120`
  - Chikou / Senkou displacement: `30`
  - RSI direction gate: `rsi > 50` / `rsi < 50`
  - Volatility gate: `MovingAverage > 0.2`
- Current honest first pass: derive `3h` bars from contiguous `1h` Hyperliquid candles and keep that resampling policy explicit in every evaluation artifact.

## Venue fit
- Primary venue: Hyperliquid perps.
- Candidate instrument: `ETH-PERP`.
- Why not Polymarket: this is a directional crypto bar strategy, not a binary-market thesis.

## Implementation requirements
- New HL-native strategy slug/module; do not modify an existing registered strategy in place.
- Wire deterministic `1h -> 3h` resampling support through the existing Hyperliquid archive/backtest path.
- Keep mechanical readiness separate from research verdicts.
- Include funding and fee effects in evaluation.

## Main risks
- Direct TradingView extraction was blocked; provenance is strong but comes from a secondary public mirror.
- `3h` support is not yet wired in the current runtime path.
- Hidden Pine defaults beyond the recovered values may still matter for parity.

## Acceptance criteria
- Folder-style hypothesis is registered and verified in the lifecycle DB.
- The Hyperliquid backtest path accepts `3h` honestly via strict `1h -> 3h` resampling.
- A bounded HL-native implementation and backtest produce durable artifacts for ETH-PERP.
- Research verdict states whether the strategy remains credible after honest cadence, fee, and funding assumptions.
