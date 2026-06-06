# AlphaInsider Tracer Bullet — 2026-06-05

Goal: test whether the AlphaInsider -> TradingView -> Pine -> Trading-Lab clone funnel is practical before committing to the full intake program.

## Sample tested

1. RSI2 Scapling
   - AlphaInsider: `https://alphainsider.com/strategy/aVXQLQ3bIoRGuf_SfeqC9?timeframe=year`
   - TradingView: `https://www.tradingview.com/script/27mK1oIF-RSI2-Scapling/`
   - Pine editor: `https://www.tradingview.com/pine/?id=PUB%3B3VYh0Nbd1GRLb2PiSps5zmVacbRFWRYE`
2. Noro's TrendMA Strategy
   - AlphaInsider: `https://alphainsider.com/strategy/1HiN6rvVcYe12qpBTzPZF?timeframe=year`
   - TradingView link exposed by AlphaInsider: `https://www.tradingview.com/script/BRDFaufy-noro-s-trendma-strategy/`
3. Ichimoku Kinko Hyo: ETH 3h Strategy
   - AlphaInsider: `https://alphainsider.com/strategy/Cth7pfZgpTqh9lubqpTIw?timeframe=year`
   - TradingView link exposed by AlphaInsider: `https://www.tradingview.com/script/NFUkMBs6-Ichimoku-Kinko-Hyo-ETH-3h-Strategy-by-tobuno/`

## What worked

### 1. AlphaInsider is good enough for a first-pass catalog
- The public search page exposes strategy names, links, subscriber counts, and day/week/month/year/5Y return slices.
- Individual strategy pages expose:
  - narrative description or explicit rules
  - TradingView link
  - creation date
  - some current-holdings clues
- This is enough to build a durable local universe catalog and first-pass scorecard.

### 2. At least some strategies are cloneable from public data
For `RSI2 Scapling`, we recovered both rules and Pine source.

AlphaInsider rules:
1. Price is above its 200-period moving average
2. The 2-period RSI of price closes below 5
3. Buy price on the close
4. Exit when price closes above its 5-period moving average

Recovered Pine source via browser automation:
```pine
//@version=3
strategy("RSI-2 Strategy", overlay=true)

if (rsi(close,2) < 5 and close > sma(close,200))
    strategy.entry("BUY", strategy.long)
if (close > sma(close,5))
    strategy.close("BUY")
```

This is a strong positive signal: some AlphaInsider ideas can be ported with low inference risk.

### 3. Trading-Lab already has useful destination infrastructure for crypto clones
The repo already contains bar-driven Hyperliquid support, including:
- `src/trading_lab/runner/hl_backtest.py`
- `src/trading_lab/data/hl_bar_loader.py`
- existing HL strategy modules such as:
  - `hl_donchian.py`
  - `hl_bollinger_mr.py`
  - `hl_funding_carry.py`
- interval support in bar loader includes `1m`, `5m`, `15m`, `1h`, `4h`, `1d`

That means the destination stack for bar-based crypto strategies is real; this is not a venue-infra dead end.

## Gaps discovered

### Gap 1 — TradingView link rot is real
Two sample AlphaInsider pages pointed at TradingView publication URLs that now return `Publication not found` / HTTP 404:
- Noro's TrendMA Strategy
- Ichimoku Kinko Hyo: ETH 3h Strategy

Impact:
- We cannot assume every AlphaInsider leaderboard entry remains source-recoverable.
- The plan needs an explicit stale-link / dead-publication filter.

### Gap 2 — Direct HTTP fetch is not enough for Pine recovery
For `RSI2 Scapling`:
- plain HTTP fetch of the Pine editor page returned HTML, but not easily extractable source
- browser automation was required to read the editor textbox content cleanly

Impact:
- The intake workflow needs a browser-assisted recovery path, not just `web_extract` / `requests`.
- Source recovery should explicitly distinguish:
  - rules visible on page
  - TradingView page accessible
  - Pine editor accessible
  - code actually recoverable

### Gap 3 — AlphaInsider performance is screening data, not validation data
The public pages expose headline returns and some holdings snapshots, but not enough to trust the reported performance mechanically.

Impact:
- We should use AlphaInsider returns only for triage.
- Final selection must be driven by rule transparency + cloneability + our own validation, not leaderboard rank alone.

### Gap 4 — Some strategies may be implementable only with moderate inference risk
When the TradingView script is gone but AlphaInsider still has a prose summary, we may be able to infer a clone, but fidelity becomes weaker.

Impact:
- We need a cloneability classification:
  - cloneable now
  - cloneable with inference risk
  - not cloneable

## Verdict

## Verdict: PARTIAL

### Why PARTIAL
- The overall funnel is viable.
- We successfully proved that at least one candidate (`RSI2 Scapling`) can be recovered end-to-end from AlphaInsider to Pine source.
- But the source-recovery path is not uniform: stale TradingView links and browser-only Pine extraction are real operational gaps.

## Recommended changes to the approach

1. Keep the overall plan.
2. Add a hard early gate in Phase 3:
   - if source recovery fails, downgrade the candidate immediately
3. Split source recovery status into four fields in the catalog:
   - `tv_link_status`
   - `tv_page_accessible`
   - `pine_editor_accessible`
   - `source_code_recovered`
4. Prefer the first shortlist from candidates with:
   - open-source Pine actually recoverable
   - simple bar-based logic
   - clear asset/timeframe mapping
5. Use `RSI2 Scapling` as the canonical first cloneability benchmark.

## Best next step

Proceed with the catalog ticket, but add recovery-status fields from day one. The first serious shortlist should be biased toward source-recoverable strategies rather than raw AlphaInsider returns.