---
slug: hl-donchian
source: manual
source_url: null
created: 2026-05-30
parent_slug: null
state: BACKTEST
venue: hyperliquid
bar_interval: 1h
universe_as_of: 2026-05-30
universe_tiers: [tier_1, tier_2]      # top 10 by 30d notional volume
backfill_start: 2024-05-30
funding_aware: true
strategy_module: trading_lab.strategies.hl_donchian
strategy_class: DonchianBreakoutStrategy
strategy_config_class: DonchianBreakoutConfig
---

# Donchian Channel Breakout on Hyperliquid Perps

## Hypothesis
Crypto markets exhibit episodic trends driven by funding cycles, retail
flows, and news catalysts. A breakout above (below) the prior N-bar high
(low) is a stronger-than-random signal that a trend is underway.

## Edge claimed
Trend-following persistence in cross-sectional crypto perps. Effect size
varies by asset (majors trend more cleanly than alts) and regime
(bullish bias > sideways). Walk-forward across multiple markets gates the
"works on a single chart by luck" failure mode.

## Required data
- 1h OHLCV bars for tier_1 + tier_2 coins (top 10 by 30d notional vol).
- Funding history (1h) for the same coins to net out perp carry costs.

## Parameter space
- `entry_lookback`: [12, 24, 48, 96]
- `exit_lookback`: [6, 12, 24, 48]
- `notional_usdc`: [500, 1000, 2000]
- `cooldown_bars`: [1, 4]

## Acceptance criteria
- DSR probability ≥ 0.90 against benchmark Sharpe = 0.
- PBO ≤ 0.5.
- Max parameter CV ≤ 0.6 across WF folds.
- Mean OOS Sharpe ≥ 0.7 across ≥ 5 folds.
- Win rate × win/loss ratio ≥ 1.1 (positive expectancy).
- Max drawdown ≤ 25%.
