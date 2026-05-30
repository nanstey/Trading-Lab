---
slug: hl-bollinger-mr
source: manual
source_url: null
created: 2026-05-30
parent_slug: null
state: BACKTEST
venue: hyperliquid
bar_interval: 1h
universe_as_of: 2026-05-30
universe_tiers: [tier_1, tier_2]
backfill_start: 2024-05-30
funding_aware: true
strategy_module: trading_lab.strategies.hl_bollinger_mr
strategy_class: BollingerMRStrategy
strategy_config_class: BollingerMRConfig
---

# Bollinger Z-score Mean Reversion on Hyperliquid Perps

## Hypothesis
On time-frames where information flow is fast (1h on crypto majors),
extreme deviations from a rolling SMA tend to mean-revert. A 2σ stretch
should partially fill within a small number of bars more often than not.

## Edge claimed
Statistical mean reversion in liquid perpetual markets. The opposite of
the trend hypothesis; we let walk-forward + DSR decide which works on
which markets/regimes.

## Required data
- 1h OHLCV bars for tier_1 + tier_2 coins.
- Funding history for cost attribution.

## Parameter space
- `lookback`: [24, 48, 96, 168]
- `entry_z`: [1.5, 2.0, 2.5, 3.0]
- `exit_z`: [0.0, 0.25, 0.5]
- `notional_usdc`: [500, 1000, 2000]
- `max_hold_bars`: [24, 48, 96]

## Acceptance criteria
- DSR probability ≥ 0.90.
- PBO ≤ 0.5.
- Max parameter CV ≤ 0.6 across WF folds.
- Mean OOS Sharpe ≥ 0.7.
- Profit factor ≥ 1.2.
- Max drawdown ≤ 20%.
