---
slug: hl-donchian-alts-trial
source: hl-donchian-optimisation-2026-05-30
source_url: null
created: 2026-05-30
parent_slug: hl-donchian
state: PAPER_READY
venue: hyperliquid
bar_interval: 1h
universe_as_of: 2026-05-30
universe_coins: [ZEC, NEAR]            # explicit, not tier-derived — see body
backfill_start: 2024-05-30
funding_aware: true
strategy_module: trading_lab.strategies.hl_donchian
strategy_class: DonchianBreakoutStrategy
strategy_config_class: DonchianBreakoutConfig
strategy_params:
  entry_lookback: 48
  exit_lookback: 24
  notional_usdc: 2000
  cooldown_bars: 1
  long_only: false
paper_trial:
  start_date: 2026-05-30
  duration_days: 30
  stop_rule: "14 consecutive days of negative cumulative paper PnL"
  next_review: 2026-06-29
---

# Donchian Breakout — ZEC/NEAR Paper Trial

## Why this is PAPER_READY (and what that means here)

The `hl-donchian` optimisation across the top 5 markets returned an OOS mean
Sharpe of **+1.28** with min 53 trades per fold and total OOS PnL of
**+$3,106** — but PBO across the 4 walk-forward folds was 1.0, telling us
the search picked different "best" configs per regime. The same single
config across the broader top-10 only breaks even, because the winners
(ZEC, NEAR) carry the losers (HYPE, XLM, XMR).

This trial **does not claim a universal edge**. It tests one specific
hypothesis: *"the Donchian (48/24) edge that historically appeared on
ZEC and NEAR survives forward 30 days of live conditions."* If it does,
we widen the universe and re-evaluate. If it doesn't, this hypothesis is
retired and the system has correctly avoided a real loss.

## Strategy parameters (frozen for the trial)

- `entry_lookback`: 48 hours
- `exit_lookback`: 24 hours
- `notional_usdc`: 2000 per entry
- `cooldown_bars`: 1
- Bars: 1h, NETTING account, MARGIN, MakerTakerFeeModel @ 1.5/4.5 bps

## Universe (frozen)

`ZEC`, `NEAR`. Selected because both showed standalone OOS Sharpe > 1.5 on
the backtest window. Not derived from a tier — chosen *because they worked
historically*, which is explicit selection bias we're calling out and
testing forward.

## Stop / promote / retire rules

| Condition | Action |
|---|---|
| 14 consecutive days cumulative paper PnL < 0 | Stop trial. Retire to SHELVED. |
| Cumulative paper PnL > +1% of allocated capital at day 30 | Re-evaluate. Either widen universe, or extend trial 60 more days. |
| Realised win-rate < 25% with any losing trade > $200 | Stop. Investigate execution / slippage. |
| `paper_reports/` shows max-DD > 5% intra-day | Pause, re-check sizing. |

## Historical reference (from optimisation run 2026-05-30)

| Coin | OOS PnL (Nov-2025 → May-2026) | OOS Sharpe | Fills |
|---|---|---|---|
| ZEC  | +$2,572 | +2.67 | 108 |
| NEAR | +$1,126 | +1.94 | 89  |

**Combined: +$3,698 over 6 months on $20,000 deployed = +18.5% with Sharpe ~ 2.3.**

This is the number to beat — or fail to beat — in the next 30 days.

## How to run the paper trial

```bash
# Single backtest sanity (should match historical numbers above)
python scripts/hl_backtest.py --hypothesis-slug hl-donchian-alts-trial \
    --coins ZEC,NEAR \
    --start 2025-11-03 --end 2026-05-30 \
    --strategy-params-json '{"entry_lookback":48,"exit_lookback":24,"notional_usdc":2000,"cooldown_bars":1}'

# Paper deployment via existing HL runner (per HL paper/testnet runbook).
# Cap per-strategy USDC at 4000 in config/portfolio.yaml under
# `hl-donchian-alts-trial` before launching.
```
