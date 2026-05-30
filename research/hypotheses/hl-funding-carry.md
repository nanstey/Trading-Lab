---
slug: hl-funding-carry
source: manual
source_url: null
created: 2026-05-30
parent_slug: null
state: BACKTEST
venue: hyperliquid
bar_interval: 1h
universe_as_of: 2026-05-30
universe_tiers: [tier_1, tier_2, tier_3]   # broad universe — carry decays on majors
backfill_start: 2024-05-30
funding_aware: true
strategy_module: trading_lab.strategies.hl_funding_carry
strategy_class: FundingCarryStrategy
strategy_config_class: FundingCarryConfig
---

# Hourly Funding Carry on Hyperliquid Perps

## Hypothesis
When perp funding diverges meaningfully from zero, the side that receives
funding earns positive carry. Over many bars, expected return from carry
exceeds the average price PnL of holding the receiving side — provided
exits are timed before regime shifts and price moves stay within the ATR
band the strategy was trained on.

## Edge claimed
Perp-specific carry edge. Real on HL because funding rates can sustain
multi-day periods >1 bps/hour (>8% APR). Risk is asymmetric price moves;
mitigated by max_hold_bars, stop_loss_pct, and atr_skip_pct gates.

## Required data
- 1h OHLCV bars for tier_1 + tier_2 + tier_3 coins.
- Full hourly funding history per coin (not optional — strategy hard-loads
  this in on_start).

## Parameter space
- `entry_threshold_bps`: [0.5, 1.0, 2.0, 4.0]
- `exit_threshold_bps`: [0.0, 0.25, 0.5]
- `notional_usdc`: [500, 1000, 2000]
- `stop_loss_pct`: [1.5, 2.5, 5.0]
- `max_hold_bars`: [24, 48, 96]
- `atr_skip_pct`: [3.0, 5.0, 8.0]

## Acceptance criteria
- DSR probability ≥ 0.90.
- PBO ≤ 0.5.
- Max parameter CV ≤ 0.6 across WF folds.
- Mean OOS Sharpe ≥ 0.7.
- Funding PnL should be > 0 on majority of folds (positive carry capture).
- Max drawdown ≤ 25%.
