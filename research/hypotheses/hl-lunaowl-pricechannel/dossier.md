---
slug: hl-lunaowl-pricechannel
venue: hyperliquid
source: alphainsider
source_url: https://alphainsider.com/strategy/OZKJvQzBe94tyDZGSGTJ0?timeframe=year
created: 2026-06-06
parent_slug: null
state: PROPOSED
bar_interval: 1d
funding_aware: true
market_criteria:
  venue: hyperliquid
  symbols: [BTC, ETH]
  preferred_timeframe: 1d
  candidate_timeframes: [1d]
  requires_perps: true
  requires_stop_fill_honesty: true
strategy_module: trading_lab.strategies.hl_lunaowl_pricechannel
strategy_class: HLLunaOwlPriceChannelStrategy
strategy_config_class: HLLunaOwlPriceChannelConfig
strategy_params:
  channel_length: 21
  notional_usdc: 1000.0
  allow_short: true
  exit_on_midline_reentry: false
---

# hl-lunaowl-pricechannel

## Hypothesis
Clone the AlphaInsider / TradingView LunaOwl PriceChannel strategy as a new Hyperliquid-perp hypothesis on BTC-PERP and ETH-PERP. The recovered rule set is a dual-direction breakout system around a 21-period price channel: derive upper/lower rails from `highest(high, Channel_Length)` and `lowest(low, Channel_Length)`, place stop-style entries at the channel boundaries, and allow both long and short participation. The first-pass research destination is Hyperliquid paper only after a later HL-native implementation, targeted tests, smoke validation, and bounded backtest evidence.

## Source evidence
- AlphaInsider strategy id: `OZKJvQzBe94tyDZGSGTJ0`
- AlphaInsider page: `https://alphainsider.com/strategy/OZKJvQzBe94tyDZGSGTJ0?timeframe=year`
- Linked TradingView script: `https://www.tradingview.com/script/gBlsZ02G-LunaOwl-LOHAS-Investor-PriceChannel/`
- Recovery artifacts:
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v3.md`
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v9.csv`
  - `research/captures/alphainsider/2026-06-06-crypto-clone-feasibility-v1.md`
  - `research/captures/alphainsider/2026-06-06-crypto-finalist-recommendation-v1.md`

## Recovered rule sheet
- Strategy family: dual-direction price-channel breakout.
- Recovered implementation details from public mirrors:
  - `strategy("[LunaOwl] 價格通道")`
  - default `Channel_Length = 21`
  - channel rails from `highest(high, Channel_Length)` and `lowest(low, Channel_Length)`
  - `gapUp` / `gapDown` state coloring on the channel transitions
  - stop entries at `Channel_High` and `Channel_Low`
  - both `strategy.long` and `strategy.short` paths are present
- Current honest first pass: prioritize a slower-cadence breakout implementation with explicit stop-fill realism rather than assuming touch-at-level fills.

## Venue fit
- Primary venue: Hyperliquid perps.
- Candidate instruments: `BTC-PERP`, `ETH-PERP`.
- Why not Polymarket: this is a directional breakout strategy, not a binary-market thesis.

## Implementation requirements
- New HL-native strategy slug/module; do not modify an existing registered strategy in place.
- Explicit stop-entry simulation assumptions; channel-touch fills must not be treated as free liquidity.
- Deterministic bar-close / breakout evaluation with fees and funding included in research.
- Targeted parity checks for channel construction, breakout triggers, and long/short symmetry.

## Main risks
- Provenance is a secondary public mirror rather than direct TradingView source extraction.
- Breakout systems are highly sensitive to fill modeling, especially on gap-through moves.
- The recovered public rule sheet is strong, but exact defaults beyond the core channel logic may still require conservative reconstruction.

## Acceptance criteria
- HL-native implementation exists under a new slug/module with targeted tests.
- Smoke validation passes on the actual module chosen for implementation.
- Bounded Hyperliquid backtest produces durable artifacts and explicitly reports the impact of stop-fill assumptions, fees, and funding.
- Research verdict states whether the strategy remains credible after honest breakout execution assumptions.
