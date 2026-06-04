---
slug: polymarket-ladder-maker
venue: polymarket
source: manual
source_url: https://github.com/Benjamin-cup/Polymarket-trading-bot-python-V2
created: 2026-06-04
parent_slug: arb-complement
state: PROPOSED
market_criteria:
  outcome_type: binary
  min_volume_24h_usdc: 5000
  min_liquidity_usdc: 2500
  require_series: false
  resolution_horizon_days: [0, 7]
  resolved: null
  count: 20
  sort_by: volume_24h_usdc
strategy_module: trading_lab.strategies.ladder_maker
strategy_class: LadderMakerStrategy
strategy_config_class: LadderMakerConfig
---

# polymarket-ladder-maker

## Hypothesis
A maker-first ladder strategy can earn edge on Polymarket binaries by resting layered sell liquidity on both YES and NO outcomes when the combined executable sale value exceeds $1.00 by enough to cover inventory risk, adverse selection, and eventual unwind cost. This is the sell-side / market-making cousin of complement arbitrage rather than a pure directional strategy.

## Edge claimed
The edge comes from:
- maker fee = zero while takers pay fees
- market-maker rebate incentives on fee-bearing markets
- temporary book imbalance and wide spreads in binary contracts
- the ability to monetize two-sided flow without predicting the ultimate outcome

## External evidence
- Polymarket runs a hybrid CLOB with offchain matching and onchain settlement, suitable for passive quote placement and cancel-replace logic.
  - https://docs.polymarket.com/trading/overview
- Polymarket supports post-only and GTD/GTC style resting orders at the API/SDK level.
  - https://docs.polymarket.com/trading/orders/create
- Makers are not charged fees, while crypto takers are; maker rebates are funded from taker fees.
  - https://docs.polymarket.com/trading/fees
- Near-expiry digital-option market making is conceptually plausible, but hedging and inventory control are difficult and spreads should widen when uncertainty is high.
  - https://www.research.hangukquant.com/p/digital-option-market-making-on-prediction

## Required data
- High-resolution top-of-book and depth on both YES and NO outcomes.
- Queue-aware fill observations or conservative fill assumptions for resting orders.
- Realized unwind cost of residual inventory near resolution.
- Market-level fee and rebate settings where available.

## Existing infrastructure
- Existing complement-arb logic and paired-inventory accounting in the repo.
- Existing Polymarket book/trade ingestion and binary strategy framework.
- Existing live/paper execution path for plain limit orders and cancellations.

## Missing infrastructure / new scripts
- Live-path support for post-only / GTD order semantics; current repo live execution signs plain limit orders only.
- Cancel-replace ladder manager for maintaining multiple passive levels per side.
- Queue/partial-fill aware paper model; current paper fill logic is full-or-nothing and too coarse for maker research.
- Inventory unwind / stair-exit module for residual YES or NO exposure.
- Selector/analytics tooling for markets with sufficient two-sided depth.

## Implementation requirements
- Multiple resting levels on both outcomes with hard aggregate inventory caps.
- Post-only quote protection and automatic expiry before resolution.
- Inventory-aware widening and quote skew as one side fills faster than the other.
- Explicit unwind logic for unmatched inventory.

## Parameter space
- `levels_per_side`: [1, 2, 3]
- `tick_spacing`: [1, 2, 3]
- `inventory_skew_ticks`: [1, 2, 4]
- `max_unpaired_shares`: [10, 25, 50]
- `quote_expiry_secs`: [10, 30, 60]

## Acceptance criteria
- Positive net maker PnL after estimated adverse selection and unwind cost.
- Fill rate sufficient to justify quote maintenance overhead.
- Residual inventory losses remain bounded under stressed conditions.
- Out-of-sample results remain positive after adding conservative queue and partial-fill assumptions.

## Research notes
This is the nearest neighbor to the existing `arb-complement` thesis, but it is not a drop-in extension. The strategy needs maker-specific order semantics and a materially more realistic fill model before research conclusions will be trustworthy.