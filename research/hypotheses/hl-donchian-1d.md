---
slug: hl-donchian-1d
source: manual
source_url: null
created: 2026-05-30
parent_slug: hl-donchian
state: BACKTEST
venue: hyperliquid
bar_interval: 1d
universe_as_of: 2026-05-30
universe_tiers: [tier_1, tier_2]
backfill_start: 2024-05-30
funding_aware: true
strategy_module: trading_lab.strategies.hl_donchian
strategy_class: DonchianBreakoutStrategy
strategy_config_class: DonchianBreakoutConfig
---

# Daily Donchian Breakout on Hyperliquid Perps

## Hypothesis
Same trend-following thesis as `hl-donchian` but on **1d bars over a 2-year
window**. The 2024-2025 stretch covers a mixed regime (rallies, drawdowns,
the late-2024 chop) so WF folds sample independent market environments —
PBO becomes a meaningful signal again, where the 6-month 1h variant didn't
have enough independent regime windows to be honest.

## Edge claimed
Trend persistence over multi-day horizons on top-10 perp markets. Daily
sampling smooths intraday microstructure noise; lookbacks measured in days
match the holding period a swing trader would use anyway.

## Required data
- 1d OHLCV bars for tier_1 + tier_2 coins (~730 days each).
- 1h funding history (re-sampled to daily for cost attribution).

## Parameter space
- `entry_lookback`: [10, 20, 30, 60]
- `exit_lookback`: [5, 10, 20]
- `notional_usdc`: [1000, 2000]
- `cooldown_bars`: [0, 1, 3]

## Acceptance criteria
- PBO ≤ 0.5 (>= 5 folds over 2y of daily data should be enough to be honest).
- Max parameter CV ≤ 0.6.
- Mean OOS Sharpe ≥ 0.7.
- Min 20 OOS trades per fold (low bar for daily — entries are infrequent).
- Max drawdown ≤ 30% (daily trend strategies can sit through chop).
