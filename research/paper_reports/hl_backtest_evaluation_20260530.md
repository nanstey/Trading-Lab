# Hyperliquid backtest evaluation — 2026-05-30

This is the first end-to-end evaluation of the new Hyperliquid backtest harness across three candidate strategies on the top-10 perp universe, with walk-forward + DSR + PBO + parameter-stability gates.

## TL;DR

- **System: working.** Backfill, multi-market backtest, walk-forward, deflated Sharpe, PBO, and parameter-stability all wired and tested.
- **Strategies tested: 3.** Donchian breakout, Bollinger Z-score mean reversion, hourly funding carry.
- **Promotion verdict: none of the three pass the strict PAPER_READY bar today.** This is the right answer given current data and is itself the point of the harness.
- **Recommended paper-trade candidate (with caveats):** Donchian (`entry=48`, `exit=24`, `notional=2000`, `cooldown=1`) on the **tier-1 (top 5)** subset, treated as a limited trial deployment to gather forward-paper data — see "Recommendation" below.

## What was built

| Component | File | Notes |
|---|---|---|
| Historical client | `src/trading_lab/venues/hyperliquid/historical.py` | candleSnapshot + fundingHistory + metaAndAssetCtxs, paginated, rate-limit aware |
| Parquet catalog | `src/trading_lab/data/hl_catalog.py` | separate tree from Polymarket, plain dir partitioning |
| Universe snapshot | `src/trading_lab/data/hl_universe.py` + `scripts/refresh_hl_universe.py` | point-in-time top-N with tier labels |
| Bar loader | `src/trading_lab/data/hl_bar_loader.py` | Parquet → NT `Bar` events |
| Backtest runner | `src/trading_lab/runner/hl_backtest.py` | MARGIN account, MakerTakerFeeModel @ 1.5/4.5 bps, multi-market portfolio aggregation |
| Funding accounting | `src/trading_lab/research/funding.py` | post-process from position history + funding stamps |
| Metrics | `src/trading_lab/research/metrics.py` | Sharpe, Sortino, Calmar, profit factor, expectancy, win/loss, max DD, decomposition |
| Walk-forward | `src/trading_lab/research/walk_forward.py` | anchored/rolling, embargo, min train/test floors |
| Anti-overfitting | `src/trading_lab/research/overfitting.py` | DSR (Bailey/Lopez de Prado), PBO via CSCV, parameter-stability CV |
| Optimizer | `scripts/hl_optimize.py` | grid sweep × WF folds, tiered decision rules |
| Tests | `tests/research/`, `tests/data/test_hl_catalog.py` | 35 tests, all passing |

## Data coverage

Backfilled from HL mainnet for **top-20 by 30d notional volume** (snapshot `2026-05-30`):

| Resolution | Rows total | Per-coin range | Notes |
|---|---|---|---|
| 1d | ~12,700 | up to 731 days each | HL stores ≥ 2 years for daily |
| 1h | ~97,700 | up to 4,985 (~7 months) | HL caps each candleSnapshot at 5000 rows |
| 5m | ~96,300 | ~4,800 (~17 days) | HL hard cap "most recent 5000 candles" makes 5m unusable for >17d historicals |
| Funding (1h) | ~273,000 | 17,500 per coin where available | hourly cadence, full 2-year window |

Top 20 universe (current snapshot): BTC, HYPE, ETH, ZEC, NEAR, SOL, XRP, XLM, LIT, XMR, BNB, WLD, ASTER, SUI, TON, VVV, INJ, XPL, DOGE, TAO.

## Strategy results (4-fold anchored WF, 24-config grid, top-5 universe)

| Hypothesis | Bar | Best OOS Sharpe | Best OOS PnL | Min OOS trades | DSR prob | PBO | Param CV | Decision |
|---|---|---|---|---|---|---|---|---|
| `hl-donchian` | 1h | **+1.28** | +$3,106 | 53 | 0.062 | 1.00 | 0.40 | REJECTED (overfit_pbo) |
| `hl-donchian-1d` | 1d | -1.27 | -$6,053 | 18 | 0.004 | **0.10** | 0.90 | REJECTED (negative OOS) |
| `hl-bollinger-mr` | 1h | -1.76 | -$197 | 14 | 0.005 | 1.00 | 0.55 | REJECTED (overfit_pbo) |
| `hl-funding-carry` | 1h | 0.0 | $0 | 0 | 0.024 | 1.00 | 0.00 | REJECTED (too_few_trades) |

### Reading the numbers

- **Donchian 1h** has the most promising OOS Sharpe (+1.28 across 4 folds on 5 coins) but PBO = 1.0 means the per-fold best config is volatile — 6 months / 4 folds doesn't sample enough independent regimes for the search to be honest.
- **Donchian 1d over 2 years (more independent regimes)** has clean PBO (0.1) but the strategy itself is unprofitable on this universe — daily breakouts on top-10 perps don't have an edge in this window.
- **Bollinger MR** loses on every config — straight mean reversion has no edge at hourly cadence on the top-5 here.
- **Funding carry** never triggers because the smallest entry threshold (0.5 bps/hr) is still above the 99th percentile of BTC's funding distribution. The strategy needs alt-coin tiers (tier_3) or sub-bps thresholds. Future tuning required.

### Single-config sanity check — Donchian on full top-10

To remove search bias, ran the Donchian winner without optimisation across all 10 tier_1+tier_2 coins (Nov 2025 - May 2026):

| Coin | Total PnL | Sharpe | Fills |
|---|---|---|---|
| ZEC  | +$2,572 | +2.67 | 108 |
| NEAR | +$1,126 | +1.94 | 89 |
| ETH  | +$56    | +0.19 | 119 |
| XRP  | +$20    | +0.09 | 86 |
| BTC  | -$190   | -0.74 | 122 |
| SOL  | -$136   | -0.32 | 133 |
| LIT  | -$204   | -0.30 | 67 |
| XMR  | -$884   | -2.13 | 125 |
| HYPE | -$1,041 | -1.83 | 136 |
| XLM  | -$1,354 | -3.76 | 33 |

**Portfolio: ~break-even** (Sharpe 0.00, -$157 price PnL + $121 funding = -$36 net after $881 in fees over 6 months).

The earlier top-5 optimisation favored ZEC/NEAR — sample selection inflated the result. Across the broader top-10 the edge disappears.

## Recommendation

### Provisional paper-ready candidate (explicit trial, not a promotion)

The harness is doing its job: it would have stopped us paper-trading the Donchian config based on the inflated 5-coin sample. Two honest paths to PAPER_READY:

**Path A — narrow universe trial.** Paper-trade Donchian (`entry=48`, `exit=24`, `notional=2000`, `cooldown=1`) on `{ZEC, NEAR}` only — the two coins where it has standalone Sharpe > 1.5. Treat this as a **hypothesis test**: "the edge survives forward 30 days on the markets where it worked historically." Stop if cumulative PnL goes negative for 14 consecutive days. This is **not** a promoted strategy; it's a controlled experiment.

**Path B — wait for more data.** With the harness in place, every additional month of HL history makes the WF + PBO more honest. Re-run the optimizer monthly; promote when DSR ≥ 0.5 and PBO ≤ 0.5 simultaneously hold across ≥ 6 folds.

### Strategy backlog (not built today)

- **Cross-sectional momentum**: rank top-N coins by N-day return, long top quartile / short bottom quartile, rebalance weekly. Standard crypto-perp edge that's hard to overfit because it's parameter-light.
- **Volatility breakout (Keltner channel)**: similar to Donchian but with ATR-scaled bands.
- **Funding carry on tier_3 alts**: re-aim the existing strategy at coins with funding rates that actually move (top-3 by funding volatility per month).
- **Liquidity-adjusted mean reversion**: ours uses raw z-score; weight by recent volume or order-book imbalance.

### System gaps to address

- **5m bars beyond 17 days** require a live capture daemon — HL's `candleSnapshot` won't backfill. Out of scope for this iteration; flag for future work.
- **Optuna integration**: grid sweep is fine for ≤ 100 configs. For larger spaces add Optuna TPE behind the same interface.
- **Daily archive cadence**: keep `make hl-capture-daily` on cron so the 5m/1h catalog compounds over time and the universe snapshot history stays fresh. The historical API alone will not extend intraday depth.

## Reproduction

```bash
# Re-pull data (idempotent)
python scripts/download_hyperliquid_data.py \
    --coins BTC,HYPE,ETH,ZEC,NEAR,SOL,XRP,XLM,LIT,XMR,BNB,WLD,ASTER,SUI,TON,VVV,INJ,XPL,DOGE,TAO \
    --intervals 5m,1h,1d --start 2024-05-30 --end 2026-05-30 \
    --include-funding --concurrency 2

python scripts/refresh_hl_universe.py --as-of 2026-05-30 --top-n 20

# Re-run the optimisation
for slug in hl-donchian hl-bollinger-mr hl-funding-carry; do
    python scripts/hl_optimize.py --slug $slug \
        --data-start 2025-11-03 --data-end 2026-05-30 \
        --n-folds 4 --max-configs 24 --max-coins 5
done

# Single-config Donchian sanity across top-10
python scripts/hl_backtest.py --hypothesis-slug hl-donchian \
    --start 2025-11-03 --end 2026-05-30 \
    --strategy-params-json '{"entry_lookback":48,"exit_lookback":24,"notional_usdc":2000,"cooldown_bars":1}'

# Tests
pytest tests/research tests/data/test_hl_catalog.py --no-cov
```
