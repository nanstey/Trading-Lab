---
slug: hl-supertrend-cloud
venue: hyperliquid
source: alphainsider
source_url: https://alphainsider.com/strategy/DEAE3_C84KpBBDbiy_mYW?timeframe=year
created: 2026-06-06
parent_slug: null
state: PROPOSED
bar_interval: 4h
funding_aware: true
market_criteria:
  venue: hyperliquid
  symbols: [BTC, ETH]
  preferred_timeframe: 4h
  candidate_timeframes: [5m, 4h]
  requires_perps: true
  requires_bar_close_fidelity: true
strategy_module: trading_lab.strategies.hl_supertrend_cloud
strategy_class: HLSuperTrendCloudStrategy
strategy_config_class: HLSuperTrendCloudConfig
strategy_params:
  fast_multiplier: 3.0
  fast_atr_length: 10
  slow_multiplier: 6.0
  slow_atr_length: 10
  notional_usdc: 1000.0
  allow_short: true
  flatten_on_inside_cloud: true
---

# hl-supertrend-cloud

## Hypothesis
Clone the AlphaInsider / TradingView SuperTrend Cloud strategy as a new Hyperliquid-perp hypothesis on BTC-PERP and ETH-PERP. The operative rule set recovered from public secondary sources is: maintain a cloud between two SuperTrend lines, go long on cloud crossover, go short on cloud crossunder, and flatten when price/action returns inside the cloud. The current first-pass research destination is Hyperliquid paper only after a later HL-native implementation, targeted tests, smoke validation, and bounded backtest evidence.

## Source evidence
- AlphaInsider strategy id: `DEAE3_C84KpBBDbiy_mYW`
- AlphaInsider page: `https://alphainsider.com/strategy/DEAE3_C84KpBBDbiy_mYW?timeframe=year`
- Linked TradingView script: `https://www.tradingview.com/script/sO5mkXTE-SuperTrend-Cloud-Strategy/`
- Recovery artifacts:
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v3.md`
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v9.csv`
  - `research/captures/alphainsider/2026-06-06-crypto-clone-feasibility-v1.md`
  - `research/captures/alphainsider/2026-06-06-crypto-finalist-recommendation-v1.md`

## Recovered rule sheet
- Strategy family: dual-SuperTrend cloud crossover.
- Recovered logic: treat the gap between two SuperTrend lines as a cloud; enter long on crossover, short on crossunder, and exit/flatten when price or action moves back inside the cloud.
- Recovered parameter presets from public summaries:
  - `10,6,6,10` for `5m`
  - `3,10,6,10` for `4h`
  - BTC-specific `4h` preset noted as `2.4,4,4.8,4`
- Current honest first pass: prioritize the `4h` variant because it fits existing Hyperliquid archive assumptions more cleanly than a high-turnover `5m` clone.

## Venue fit
- Primary venue: Hyperliquid perps.
- Candidate instruments: `BTC-PERP`, `ETH-PERP`.
- Why not Polymarket: this is a directional crypto trend strategy, not a binary-market thesis.

## Implementation requirements
- New HL-native strategy slug/module; do not modify an existing registered strategy in place.
- Deterministic bar-close evaluation; no optimistic intrabar crossover fills.
- Explicit fee, funding, and basis handling in evaluation.
- Targeted parity checks for the cloud-state transitions and flatten-inside-cloud behavior.

## Main risks
- Clone parity is good but not perfect because direct Pine extraction was blocked by reCAPTCHA; provenance is secondary-source recovery plus a GitHub implementation that cites the same TradingView script.
- Performance is likely sensitive to bar-close semantics, fee drag, and whether the BTC-specific 4h preset generalizes to ETH.
- A naive bar-touch fill model would overstate performance.

## Acceptance criteria
- HL-native implementation exists under a new slug/module with targeted tests.
- Smoke validation passes on the actual module chosen for implementation.
- Bounded Hyperliquid backtest produces durable artifacts and explicitly reports the effect of fees/funding assumptions.
- Research verdict states whether the strategy remains credible after honest bar-close and fill assumptions.
