---
slug: polymarket-endcycle-sniper
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
strategy_module: trading_lab.strategies.endcycle_sniper
strategy_class: EndcycleSniperStrategy
strategy_config_class: EndcycleSniperConfig
---

# polymarket-endcycle-sniper

## Hypothesis
In Polymarket 5-minute crypto Up/Down markets, the final 1-3 seconds before resolution occasionally contain stale or slow-to-update quotes relative to the official resolver path. A highly selective sniper that only trades when the likely final resolver print is already strongly implied by external data can capture late mispricings before the book fully reprices.

## Edge claimed
This is not directional forecasting in the usual sense. The claimed edge is late-window microstructure inefficiency:
- the contract resolves on a binary threshold
- market makers may leave stale quotes near the boundary
- public market data and resolver-adjacent data may update faster than resting liquidity
- a selective taker can monetize the lag if the edge exceeds delay, spread, and fees

## External evidence
- Polymarket BTC 5m rules resolve on Chainlink BTC/USD and define Up as ending price >= starting price.
  - https://polymarket.com/event/btc-updown-5m-1779055800
- Polymarket RTDS exposes both Binance and Chainlink crypto price streams.
  - https://docs.polymarket.com/market-data/websocket/rtds
- Polymarket market WebSocket exposes book, price change, trade, and best bid/ask events.
  - https://docs.polymarket.com/market-data/websocket/market-channel
- Polymarket order lifecycle includes a 250 ms taker delay on selected crypto/finance up/down markets, which directly attacks naive sniping.
  - https://docs.polymarket.com/concepts/order-lifecycle
- Continuous order books are structurally vulnerable to stale-quote sniping, but the edge is capacity-constrained and fragile.
  - Budish, Cramton, Shim: https://www.econ.umd.edu/sites/www.econ.umd.edu/files/pubs/budish-cramton-shim-frequent-batch-auctions-aerpp.pdf
  - Cohen, Szpruch: https://people.maths.ox.ac.uk/cohens/papers/lobFINAL.pdf

## Required data
- Polymarket 5m crypto market metadata, including token IDs, resolution windows, and any market-level fee/taker-delay flags.
- Full final-window top-of-book and trade tape for both outcomes.
- Chainlink BTC/USD timestamps and values around market open and close.
- Fast proxy BTC spot feed (for example Binance) to estimate lead-lag versus Chainlink.
- Realized fill / reject / cancel timing from paper or live testing.

## Existing infrastructure
- Existing Polymarket market WS client and trade/book ingestion.
- Existing order submission, cancel, and paper/live runner framework.
- Existing market catalog and historical Polymarket trade ingest.

## Missing infrastructure / new scripts
- Dedicated selector for 5m crypto Up/Down markets. Current market catalog metadata is too sparse to isolate them cleanly by `market_criteria` alone.
- RTDS ingestion + archival for Chainlink and Binance reference prices.
- Delay-aware replay/backtest harness that models the 250 ms taker delay, spread, and fee drag.
- Final-seconds event-study script to align book state with resolver path.
- Stronger paper fill semantics for last-window latency-sensitive trading.

## Implementation requirements
- Exact resolver-aware logic keyed to Chainlink, not just exchange spot.
- Millisecond timestamp alignment across Polymarket, Chainlink, and proxy spot feeds.
- Hard no-trade rules when expected edge does not clearly exceed spread + fee + delay.
- Maximum one-shot risk limits per market and strict cutoff windows.

## Parameter space
- `entry_secs_to_expiry`: [1, 2, 3]
- `resolver_edge_ticks`: [1, 2, 3]
- `min_book_depth_usdc`: [100, 250, 500]
- `max_taker_fee_bps_equiv`: [0.5, 1.0, 1.5]

## Acceptance criteria
- Net positive edge after modeled fees and taker delay.
- Positive expectancy in the final 1-3 second event study on at least 100 windows.
- Stable fill rate and no reliance on unrealistic zero-latency assumptions.
- Out-of-sample PnL remains positive after conservative slippage stress.

## Research notes
This idea is attractive but dangerous. The anti-sniping taker delay may kill most of the edge. The first research task is not implementation; it is measurement of whether stale late-window quotes persist after accounting for the exact resolver and the enforced delay.