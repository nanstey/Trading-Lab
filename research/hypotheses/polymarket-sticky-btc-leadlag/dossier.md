---
slug: polymarket-sticky-btc-leadlag
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
strategy_module: trading_lab.strategies.sticky_leadlag
strategy_class: StickyLeadLagStrategy
strategy_config_class: StickyLeadLagConfig
---

# polymarket-sticky-btc-leadlag

## Hypothesis
In Polymarket short-duration BTC-linked markets, large BTC spot/perp moves are incorporated first in the primary crypto venues and only later in the thinner prediction-market order book. A strategy that detects Polymarket lagging repricing and trades only when the lag exceeds spread, fees, and expected fill latency can capture the convergence.

## Edge claimed
The edge is short-horizon lead-lag:
- BTC spot/perp venues lead
- Polymarket crypto contracts reprice second
- the book may remain stale for a short but tradable interval
- the strategy buys or sells the lagging Polymarket outcome before repricing completes

## External evidence
- Polymarket exposes executable market-data streams through the public WebSocket and trades through a hybrid CLOB.
  - https://docs.polymarket.com/market-data/websocket/overview
  - https://docs.polymarket.com/market-data/websocket/market-channel
  - https://docs.polymarket.com/trading/overview
- Prediction markets can display meaningful price disparities and delayed information incorporation under liquidity segmentation.
  - SSRN: https://papers.ssrn.com/sol3/Delivery.cfm/5331995.pdf?abstractid=5331995&mirid=1
  - Tetlock: https://business.columbia.edu/sites/default/files-efs/pubfiles/3098/Tetlock_SSRN_Liquidity_and_Efficiency.pdf
- Crypto markets often exhibit measurable lead-lag structure, with BTC leading related assets on short horizons in at least some regimes.
  - Makarov and Schoar: https://personal.lse.ac.uk/makarov1/index_files/PriceDiscoveryCrypto.pdf
  - Anderson: https://businessperspectives.org/images/pdf/applications/publishing/templates/article/assets/17735/IMFI_2023_01_Anderson.pdf
- Counterpoint: lead-lag can be weak, unstable, or hard to exploit once fees and competition are included.
  - https://doi.org/10.1016/j.ribaf.2019.06.012

## Required data
- Polymarket BTC-linked market order books, trades, and market metadata.
- Sub-second BTC spot and ideally perp data from a leading venue.
- Exact timestamp alignment between the external BTC feed and Polymarket feed.
- Historical fills or conservative fill simulation for stale-quote taker logic.

## Existing infrastructure
- Existing Polymarket market-data client and execution stack.
- Existing book/trade archival for Polymarket markets.
- Existing backtest and paper-trading strategy plumbing.

## Missing infrastructure / new scripts
- External BTC reference-feed ingestion and archival.
- Event-study script to measure repricing lag after BTC shocks.
- Contract-mapping script that ties a Polymarket market to its relevant underlying and timing semantics.
- Conservative fill model for stale-quote capture under public-feed latency.

## Implementation requirements
- Synchronized timestamps across BTC feed and Polymarket feed.
- A fair-value estimator that maps BTC move + time remaining into expected contract repricing.
- Trigger logic that ignores weak or ambiguous moves.
- Position limits tuned for taker-style latency trading.

## Parameter space
- `shock_window_ms`: [250, 500, 1000, 2000]
- `btc_move_bps`: [5, 10, 20]
- `repricing_gap_ticks`: [1, 2, 3]
- `max_hold_secs`: [1, 3, 5, 10]
- `min_liquidity_at_touch_usdc`: [100, 250, 500]

## Acceptance criteria
- Statistically significant average repricing lag after BTC shocks.
- Positive simulated PnL after fees and conservative fill assumptions.
- Edge survives out-of-sample periods and does not vanish after a small number of large moves are removed.
- Capacity estimates show the strategy is not purely theoretical at trivial size.

## Research notes
This is one of the cleanest ways to turn the README into a falsifiable hypothesis. The first task is a lag study, not coding a live trader. If Polymarket reprices within the noise floor of public-feed latency, this idea dies quickly.