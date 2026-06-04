---
slug: polymarket-btc-5m-price-field
venue: polymarket
source: manual
source_url: https://github.com/Benjamin-cup/Polymarket-trading-bot-python-V2
created: 2026-06-04
parent_slug: null
state: PROPOSED
market_criteria:
  outcome_type: binary
  min_volume_24h_usdc: 1000
  min_liquidity_usdc: 1000
  require_series: false
  resolution_horizon_days: [0, 1]
  resolved: null
  count: 20
  sort_by: volume_24h_usdc
strategy_module: trading_lab.strategies.btc_price_field
strategy_class: BTCPriceFieldStrategy
strategy_config_class: BTCPriceFieldConfig
---

# polymarket-btc-5m-price-field

## Hypothesis
A Polymarket 5-minute BTC Up/Down contract can be modeled as a near-expiry digital option whose fair probability is a function of:
- time remaining to resolution
- current BTC deviation from the contract's price-to-beat
- short-horizon realized volatility
- microstructure state of the Polymarket book

If the market-implied probability deviates enough from this calibrated probability field, the mispricing can be traded profitably.

## Edge claimed
The edge is not that price should always equal a simple Black-Scholes digital. The edge is that the market may use coarse heuristics while the bot uses:
- the exact settlement process
- a calibrated time-to-expiry probability field
- live realized volatility and reference-price drift
- executable book state rather than displayed midpoint alone

## External evidence
- Polymarket 5m BTC markets are binary contracts on whether ending price >= starting price, settled to Chainlink BTC/USD.
  - https://polymarket.com/event/btc-updown-5m-1773521100
- Polymarket RTDS provides both Binance and Chainlink feeds needed for a resolver-aware field.
  - https://docs.polymarket.com/market-data/websocket/rtds
- Polymarket price display is midpoint-based or last-trade-based depending on spread, so displayed price is not always executable probability.
  - https://help.polymarket.com/en/articles/13364488-how-are-prices-calculated
- Crypto taker fees are largest near 50c, exactly where many borderline price-field decisions occur.
  - https://docs.polymarket.com/trading/fees
- Digital event contracts are naturally modeled as binary options; near expiry, fair value becomes highly sensitive to moneyness and volatility.
  - HangukQuant: https://www.research.hangukquant.com/p/digital-option-market-making-on-prediction
- Prediction-market probabilities are not automatically calibrated across all horizons and domains, so empirical recalibration is mandatory.
  - https://arxiv.org/html/2602.19520v1

## Required data
- Exact Polymarket market windows and the corresponding opening reference price.
- Chainlink BTC/USD archival stream across the full window.
- Fast proxy BTC market feed for lead-lag and micro-move estimation.
- Polymarket order book, spread, midpoint, and last-trade history.
- Resolved outcome labels for every sampled window.

## Existing infrastructure
- Existing Polymarket trade and book ingestion.
- Existing strategy/backtest framework for binary markets.
- Existing market catalog and hypothesis lifecycle system.

## Missing infrastructure / new scripts
- Script to identify and archive recurring 5m BTC Up/Down windows from market metadata.
- External reference-price archive for Chainlink and fast BTC spot.
- Feature-generation script for `time_remaining`, `price_to_beat_gap`, and realized volatility.
- Calibration/evaluation notebook or script for fitting the price field and validating probability error.
- Custom backtest layer to trade on executable bid/ask, not displayed probability.

## Implementation requirements
- Resolver-true state definition: all features must be derived against Chainlink settlement semantics.
- Per-window feature extraction with precise timestamps.
- A conservative trading rule: only trade when modeled EV exceeds spread + fees + slippage.
- Ability to abstain when the field is near indifferent or calibration confidence is low.

## Parameter space
- `entry_threshold_prob`: [0.03, 0.05, 0.08]
- `vol_lookback_secs`: [10, 30, 60]
- `min_time_remaining_secs`: [15, 30, 60]
- `max_time_remaining_secs`: [60, 120, 180]
- `book_state_filter`: [off, spread_only, spread_plus_imbalance]

## Acceptance criteria
- Calibration error materially better than naive midpoint / last-trade probability.
- Positive net expectancy after fees on a large historical sample of 5m windows.
- Robustness across BTC regimes rather than one isolated volatility cluster.
- Out-of-sample edge remains after conservative latency and slippage stress.

## Research notes
This is the strongest modeling idea in the README set because it can be framed as a concrete, testable probability surface. The main implementation risk is not the math; it is building the exact resolver-aware dataset and proving the edge survives actual execution costs.