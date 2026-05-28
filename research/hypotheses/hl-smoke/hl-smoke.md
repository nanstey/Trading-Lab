---
slug: hl-smoke
venue: hyperliquid
source: manual
source_url: ""
created: 2026-05-27
parent_slug: null
state: PAPER_READY
market_criteria:
  venue: hyperliquid
  symbols:
    - BTC-PERP
strategy_module: trading_lab.strategies.hl_smoke
strategy_class: HLSmokeStrategy
strategy_config_class: HLSmokeConfig
---

# HL Smoke Strategy

Plumbing-only strategy that exercises the full Hyperliquid paper +
testnet path end-to-end. Every `quote_interval_secs` (default 60s),
quotes one BUY at `mid - offset_bps` and one SELL at `mid + offset_bps`,
both IOC against a single instrument (`BTC-PERP`).

There is **no edge claim**. Its sole purpose is to surface integration
issues — auth, signing, rate-limit handling, reconnect behaviour, fill
plumbing — before any real Hyperliquid strategy is written. **Never
promote past `LIVE_READY`.** Documented as such in the runbook
(`runbooks/hyperliquid-testnet.md`).

## Lifecycle expectations

- `PAPER_READY → PAPER`: human-gated, exercises the in-process fill
  engine against the live HL book (no network writes).
- `PAPER → LIVE_READY`: exercises real signing against HL testnet
  (faucet USDC) via `make live-hl-testnet HYPOTHESIS=hl-smoke`.
- `LIVE_READY → LIVE`: **forbidden** for this slug. Mainnet runs are
  reserved for real strategies that carry an edge claim.
