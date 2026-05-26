# System Architecture

> **⚠ Currency note (2026-05-25):** this doc was written before the
> Phase 5 work (agentic loop, real TradingNode paper + live runners,
> events log, paper-fill engine). The component-diagram + core
> principles below are still accurate at the macro level, but the
> module map / runtime details have moved on. For the current state:
> - **[AGENTS.md](../AGENTS.md)** — canonical module map (always updated)
> - **[docs/agentic-loop.md](agentic-loop.md)** — Phase 5 loop architecture
> - **[docs/deployment.md](deployment.md)** — runtime + operator harness
> - **[docs/getting-started.md](getting-started.md)** — fresh-machine walkthrough

## Overview

Trading Lab is an algorithmic trading system built on [NautilusTrader](https://nautilustrader.io),
targeting the Polymarket prediction market CLOB and Hyperliquid perpetual futures exchange.

The system is designed around three core principles:
1. **Identical code paths**: The same strategy code runs in backtest, paper, and live modes.
2. **Fail-safe risk management**: Multiple independent layers prevent runaway losses.
3. **Low-latency hot path**: Rust handles the cancel/replace loop; Python handles strategy logic.

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Trading Lab                                 │
│                                                                          │
│  ┌───────────────────┐    ┌──────────────────────────────────────────┐  │
│  │   External Feeds  │    │         NautilusTrader Engine            │  │
│  │                   │    │  (Rust core, Python strategy layer)      │  │
│  │  Polymarket CLOB  │──▶ │                                          │  │
│  │  WebSocket WS     │    │  ┌────────────────────────────────────┐  │  │
│  │                   │    │  │      Message Bus (Rust)            │  │  │
│  │  Hyperliquid      │──▶ │  │  OrderBookDeltas  Fills  Positions │  │  │
│  │  Perp WS          │    │  └──────────────────┬─────────────────┘  │  │
│  └───────────────────┘    │                     │                    │  │
│                           │  ┌──────────────────▼─────────────────┐  │  │
│  ┌───────────────────┐    │  │           Strategies                │  │  │
│  │   Risk Layer      │    │  │                                     │  │  │
│  │                   │    │  │  PolymarketMarketMaker              │  │  │
│  │  KillSwitch       │◀───│  │  ComplementArbStrategy             │  │  │
│  │  HeartbeatWatcher │    │  │  CrossVenueHedgeStrategy           │  │  │
│  │  PositionLimits   │    │  │  CatalystTrader                    │  │  │
│  └───────────────────┘    │  └──────────────────┬─────────────────┘  │  │
│                           │                     │                    │  │
│  ┌───────────────────┐    │  ┌──────────────────▼─────────────────┐  │  │
│  │   Data Catalog    │    │  │       Order Routing                 │  │  │
│  │                   │    │  │                                     │  │  │
│  │  Parquet/Snappy   │◀───│  │  Python: Normal ops                │  │  │
│  │  Time-partitioned │    │  │  Rust:   Cancel/replace hot path   │  │  │
│  └───────────────────┘    │  └──────────────────┬─────────────────┘  │  │
│                           └─────────────────────┼────────────────────┘  │
│                                                 │                        │
│  ┌──────────────────────────────────────────────▼──────────────────┐    │
│  │                         Venues                                  │    │
│  │                                                                  │    │
│  │  Polymarket CLOB (Polygon)       Hyperliquid Perps              │    │
│  │  REST + WebSocket                REST + WebSocket               │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Inbound: Market Data

```
Polymarket WebSocket
    │
    │  JSON messages (book, price_change, trade)
    ▼
PolymarketClient.subscribe_market()
    │
    │  Parsed PolymarketOrderBook objects
    ▼
NautilusTrader DataEngine
    │
    │  OrderBookDeltas (Rust structs)
    ▼
Strategy.on_book_update()
    │
    │  Signal evaluation
    ▼
Order decision
```

### Outbound: Order Execution

```
Strategy.on_book_update() decides to quote
    │
    │  Check kill switch and position limits
    ▼
strategy.submit_order(order)         (NT Strategy API)
    │
    ▼
PolymarketExecutionClient._submit_order
    │
    │  PortfolioAllocator.check_order(order)
    │    └─ reads exposure from NT Portfolio.net_exposures(POLYMARKET)
    │    └─ rejects + emits portfolio_alloc_breach if cap exceeded
    │
    │  (accepted)
    ▼
Paper:  hand to PolymarketPaperFillEngine (no venue call)
Live:   POST to Polymarket CLOB REST + user-channel WS
    │
    ▼
OrderFilled event on the message bus
    │
    ▼
NT Portfolio updates net_exposure for the instrument
    │
    ▼
Strategy.on_fill()
```

The allocator sits on the order-submission path for BOTH paper and live
because it gates BEFORE the `is_paper` branch in
`PolymarketExecutionClient._submit_order`. Same enforcement either way.

---

## In-Memory Design

NautilusTrader is an in-memory system. All state lives in RAM:

- **Order book**: Maintained as a sorted array of price levels
- **Positions**: Tracked as net signed USDC amounts
- **Open orders**: Hash map of order_id → order state
- **PnL**: Computed from realized fills

No database is required for live trading. Parquet files are written
asynchronously for historical analysis and backtesting only.

This design minimizes latency by avoiding I/O on the critical path.

---

## Rust/Python Boundary

The system uses a hybrid architecture:

**Python (strategy logic)**:
- Strategy signal generation
- Configuration management
- Risk checks
- Data persistence

**Rust (polyfill-rs)**:
- WebSocket connection management
- Cancel/replace hot path (<100ms target)
- JSON serialization on hot path
- Future: integer price arithmetic (branchless)

The PyO3 FFI bridge allows Python strategies to call into Rust for
latency-critical operations while keeping strategy logic in Python
where it is easier to iterate on.

---

## NautilusTrader High-Precision Mode

NautilusTrader supports a high-precision mode (`NAUTILUS_HIGH_PRECISION=1`)
that uses 128-bit integers for price and quantity representation instead
of 64-bit floats. This eliminates floating-point rounding errors for
financial calculations.

Set in Dockerfile:
```dockerfile
ENV NAUTILUS_HIGH_PRECISION=1
```

For prediction market prices (0.01 to 0.99 in increments of 0.001),
this means prices are stored as integers (e.g., 0.625 → 625) with
a known decimal precision, avoiding any rounding artifacts.

---

## Authentication Architecture

### Polymarket Two-Level Auth

```
L1 Private Key (Ethereum EOA)
    │
    │  EIP-712 signing (sign_l1_message)
    ▼
/auth/derive-api-key → L2Credentials
    │
    │  Store in .env (POLY_API_KEY, POLY_API_SECRET, POLY_API_PASSPHRASE)
    ▼
HMAC-SHA256 per request (sign_request)
    │
    │  POLY-SIGNATURE, POLY-TIMESTAMP, POLY-API-KEY headers
    ▼
Polymarket CLOB API
```

### Hyperliquid Auth

Hyperliquid uses Ethereum EIP-712 signing directly on order payloads.
No separate session credentials are needed - each order is signed
with the private key.

---

## Runner Architecture

Three execution modes share identical strategy code:

```
BacktestRunner
├── NautilusTrader BacktestEngine
├── Loads from Parquet DataCatalog
└── Simulated fills at historical prices

PaperRunner
├── NautilusTrader TradingNode (paper config)
├── Live WebSocket feeds (real market data)
└── Simulated fills (no real orders placed)

LiveRunner
├── NautilusTrader TradingNode (live config)
├── Live WebSocket feeds
└── Real order submission via PolymarketClient
```

The strategy class is identical across all three modes.
Mode selection controls only the execution engine and order routing.
