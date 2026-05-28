# Hyperliquid testnet runbook

Hyperliquid strategies follow the same lifecycle as Polymarket but with one
extra gate inserted between paper and mainnet: **a real-network shakedown
on testnet**. Testnet uses faucet USDC, so no real capital is at risk, but
the runner exercises live signing, rate-limit handling, and reconnect
behavior against an actual exchange.

## Pre-requisites

1. **Mainnet wallet** (`HL_PRIVATE_KEY`) — only required to eventually
   promote to mainnet. You can ship up to and including testnet without it.
2. **Testnet wallet** (`HL_TESTNET_PRIVATE_KEY`) — generated at
   `app.hyperliquid-testnet.xyz → More → API → Generate`. The on-screen
   private key is the only chance you get to copy it; paste it into `.env`
   immediately.
3. **Faucet USDC** — request from `app.hyperliquid-testnet.xyz/drip`.
   Refill as needed.
4. `make check-env --network testnet` → green.

## Lifecycle

```
PAPER_READY → PAPER          (paper-fill engine; no HL writes)
PAPER       → LIVE_READY     (paper acceptance criteria met)
LIVE_READY  → testnet runs   (validate signing/reconnect on real network)
LIVE_READY  → LIVE           (mainnet, real money — full triple-gate)
```

An HL strategy may sit in `LIVE_READY` for as long as needed while testnet
runs accumulate evidence. Only after testnet behaviour is satisfactory
should the strategy transition `LIVE_READY → LIVE`, which still requires:

- `LIVE_TRADING_CONFIRMED=true` in `.env`
- hypothesis state == `LIVE` (human-gated transition)
- `--i-understand-this-is-live` CLI flag (or `live-hl` Makefile target)

## Running

```bash
# Paper — in-process fills, no network writes.
make paper-hl HYPOTHESIS=<slug>

# Testnet — real signing, faucet USDC, no real money.
make live-hl-testnet HYPOTHESIS=<slug>

# Mainnet — real money. Requires triple-gate (see above).
make live-hl HYPOTHESIS=<slug>
```

## Notes

- Testnet liquidity is thin and noisy. A strategy that passes testnet does
  not automatically pass mainnet; testnet's job here is integration
  validation, not strategy validation.
- `PAPER → LIVE_READY` is **not** human-gated for HL (no env-flag required).
  The mainnet write-gate still applies on `LIVE_READY → LIVE`.
- Paper PnL bakes in a configurable taker fee
  (`config/portfolio.yaml:hyperliquid_fees.taker_bps`). If your account
  qualifies for a better tier, expect paper PnL to lag real PnL by the
  delta — document this in the hypothesis's expected-edge calculation.
