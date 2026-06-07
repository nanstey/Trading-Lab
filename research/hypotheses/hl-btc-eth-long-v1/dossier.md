---
slug: hl-btc-eth-long-v1
venue: hyperliquid
source: alphainsider
source_url: https://alphainsider.com/strategy/1pOK-VJo6NckNqPeaKTxz?timeframe=year
created: 2026-06-06
parent_slug: null
state: PROPOSED
bar_interval: 2h
funding_aware: true
market_criteria:
  venue: hyperliquid
  symbols: [BTC, ETH]
  preferred_timeframe: 2h
  candidate_timeframes: [1h, 2h]
  requires_perps: true
  requires_resampling_policy: true
strategy_module: trading_lab.strategies.hl_btc_eth_long_v1
strategy_class: HLBTCEthLongV1Strategy
strategy_config_class: HLBTCEthLongV1Config
strategy_params:
  ema_length: 20
  sma_length: 100
  slow_sma_length: 200
  atr_length: 14
  macd_fast_length: 12
  macd_slow_length: 26
  macd_signal_length: 7
  volatility_cap_pct: 2.0
  stop_loss_pct: 1.5
  notional_usdc: 1000.0
---

# hl-btc-eth-long-v1

## Hypothesis
Clone the AlphaInsider / TradingView BTC and ETH Long strategy - version 1 as a new Hyperliquid-perp hypothesis on BTC-PERP and ETH-PERP. The recovered rule set is a long-only trend/filter stack on 2h bars: require MACD plus the slow SMA, fast SMA, and EMA to be rising, require `EMA20 > SMA100 < price`, filter out high-volatility regimes with `ATR14`-based `volatilityPercentage < 2`, size fixed notional, and exit on either a 1.5% stop loss or `crossunder(ema[1], sma[1])`. The first-pass research destination is Hyperliquid paper only after a later HL-native implementation, targeted tests, smoke validation, and bounded backtest evidence.

## Source evidence
- AlphaInsider strategy id: `1pOK-VJo6NckNqPeaKTxz`
- AlphaInsider page: `https://alphainsider.com/strategy/1pOK-VJo6NckNqPeaKTxz?timeframe=year`
- Linked TradingView script: `https://www.tradingview.com/script/BTzQGau9-BTC-and-ETH-Long-strategy-version-1/`
- Recovery artifacts:
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v6.md`
  - `research/captures/alphainsider/2026-06-06-crypto-review-pool-recovery-v9.csv`
  - `research/captures/alphainsider/2026-06-06-crypto-clone-feasibility-v1.md`
  - `research/captures/alphainsider/2026-06-06-crypto-finalist-recommendation-v1.md`

## Recovered rule sheet
- Strategy family: long-only MACD / moving-average trend filter.
- Recovered public context: intended for BTC / ETH / ETHXBT on `2h` candles.
- Recovered implementation details from the bounded GitHub mirror pass:
  - `strategy(... initial_capital=1000, commission_value=0.075)`
  - `MACD(12, 26, 7)`
  - `EMA20`, `SMA100`, `SMA200`
  - `ATR14` volatility filter with `volatilityPercentage < 2`
  - long entry only when the slow SMA, SMA, EMA, and MACD are rising and `ema > sma < currentPrice`
  - exit on `currentPrice <= stopLossPrice` or `crossunder(ema[1], sma[1])`
  - fixed `$1000` position size with leverage `1`
- Current honest first pass: derive `2h` bars deliberately from `1h` Hyperliquid data and keep that resampling policy explicit in every evaluation artifact.

## Venue fit
- Primary venue: Hyperliquid perps.
- Candidate instruments: `BTC-PERP`, `ETH-PERP`.
- Why not Polymarket: this is a directional crypto trend strategy, not a binary-market thesis.

## Implementation requirements
- New HL-native strategy slug/module; do not modify an existing registered strategy in place.
- Deterministic `1h -> 2h` resampling policy with bar-close evaluation; no hidden timeframe shortcuts.
- Explicit fee, funding, and perp-vs-spot basis handling in research.
- Targeted parity checks for the volatility gate, rising-trend conditions, and stop-loss / crossunder exits.

## Main risks
- Even with strong recovered source evidence, the first implementation still ports a spot-oriented TradingView script into perp data, so funding and basis can move results materially.
- Performance may be very sensitive to the exact `2h` bar construction policy.
- A naive close-only or optimistic stop model would overstate the edge.

## Acceptance criteria
- HL-native implementation exists under a new slug/module with targeted tests.
- Smoke validation passes on the actual module chosen for implementation.
- Bounded Hyperliquid backtest produces durable artifacts and explicitly reports the effect of resampling, fees, funding, and stop-loss assumptions.
- Research verdict states whether the strategy remains credible after honest `2h` resampling and perp-specific evaluation assumptions.
