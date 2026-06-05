# HL / PM Cross-Venue Readiness Plan

> For Hermes: execute this iteratively in verified atomic layers; commit and push after each completed layer.

Goal: make Trading-Lab ready to develop, backtest, and paper trade a cross-venue arbitrage strategy spanning Hyperliquid and Polymarket.

Architecture:
- Use a canonical cross-venue hypothesis spec as the single source of truth for venue mapping and strategy wiring.
- Add runtime in layers: read-only spec/readiness inspection first, then dual-venue data/runtime scaffolding, then synchronized backtest support, then paper-trading controls.
- Keep live trading out of scope until paper evidence and hedge-failure handling are verified.

Tech stack:
- Python 3.12
- NautilusTrader TradingNode
- Polymarket and Hyperliquid venue adapters already present in `src/trading_lab/venues/`
- Repo scripts under `scripts/` with JSON stdout contracts

Human intervention / approval gates:
1. Approve the canonical cross-venue hypothesis schema before bulk authoring hypotheses against it.
2. Approve the first dual-venue paper smoke run before enabling any order placement beyond no-op / dry-run.
3. Approve promotion from backtest-capable to paper-capable once hedge-failure unwind behavior is verified.
4. Approve any future paper-to-live milestone separately; not part of this plan.

Readiness layers:
1. Canonical spec + readiness audit
   - Add shared parser/validator for cross-venue HL/PM hypotheses.
   - Add a read-only script that reports what the repo can and cannot currently do for a given spec.
   - This layer unblocks disciplined development without pretending runtime support already exists.
2. Dual-venue runtime scaffold
   - Add a cross-venue paper runner that boots both Polymarket and Hyperliquid clients in one TradingNode.
   - Start in observe-only / no-order mode with joined state logging.
3. Cross-venue strategy runtime contract
   - Define how strategies receive PM books, HL prices/books, and venue-specific fill events.
   - Replace the current TODO-only cross-venue scaffold with a real strategy config contract.
4. Synchronized backtest surface
   - Add a cross-venue backtest runner with aligned PM + HL event replay.
   - Report legging-risk and execution realism explicitly.
5. Paper-trading controls
   - Add cross-venue allocator/risk checks, hedge drift thresholds, and forced-flatten policy.
   - Add operator-facing summary/reporting for venue divergence and partial-leg incidents.

Immediate next implementation target after Layer 1:
- Layer 2 dual-venue runtime scaffold
- Success criteria:
  - one runner boots both venues
  - one strategy can subscribe to both surfaces
  - no-op smoke run logs synchronized state cleanly for a bounded duration
  - no duplicate-slot or allocator regression on existing single-venue runners
