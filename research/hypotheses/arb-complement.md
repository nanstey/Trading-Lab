---
slug: arb-complement
source: manual
source_url: null
created: 2026-05-24
parent_slug: null
state: BACKTEST
market_criteria:
  outcome_type: binary
  min_volume_24h_usdc: 20000
  min_liquidity_usdc: 5000
  categories: null
  require_series: false
  resolution_horizon_days: [1, 365]
  resolved: null
  # Arb only makes sense on roughly-balanced books — extreme markets
  # (probabilities near 0/1) have ask-sum well above 1 and never cross.
  yes_prob_range: [0.30, 0.70]
  count: 5
  sort_by: volume_24h_usdc
strategy_module: nautilus_predict.strategies.arb_complement
strategy_class: BinaryArbStrategy
strategy_config_class: BinaryArbConfig
---

# Complement Arbitrage on Polymarket Binary Markets

## Hypothesis
For a Polymarket binary market, the YES + NO best-ask prices must sum to
$1.00 at resolution. Any quoted moment where `ask(YES) + ask(NO) < 1.00 - fees`
is a risk-free arb (buy both legs, hold to resolution, collect $1.00).

## Edge claimed
Microstructure inefficiency on Polymarket binary pairs. The dual-token CLOB
design (separate book per outcome) means quote-staleness on one leg shows up
as a complement-arb signal — empirically observable during high-volume events.

## Required data
- Trade history + reconstructed/snapshot book state for YES and NO tokens
  of selected markets.
- Markets selected via `market_criteria` above (deep liquidity, active flow).

## Parameter space
- `min_profit_usdc`: [0.01, 0.02, 0.05]
- `max_capital_usdc`: [100, 500, 1000]

## Acceptance criteria
- Sharpe (in-sample) ≥ 1.0; OOS Sharpe ≥ 0.7.
- Max drawdown ≤ 20%.
- ≥ 30 trades per 30-day window per market.
