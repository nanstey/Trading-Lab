# Nautilus-Predict

Algorithmic trading system for [Polymarket](https://polymarket.com) and [Hyperliquid](https://hyperliquid.xyz) built on [NautilusTrader](https://nautilustrader.io).

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Nautilus-Predict                              │
│                                                                      │
│  ┌─────────────┐    ┌─────────────────────────────────────────────┐ │
│  │  Data Feeds │    │           NautilusTrader Engine             │ │
│  │             │    │                                             │ │
│  │ Polymarket  │───▶│  ┌──────────────────────────────────────┐  │ │
│  │  WS CLOB    │    │  │         Message Bus (Rust)           │  │ │
│  │             │    │  │                                      │  │ │
│  │ Hyperliquid │───▶│  │  OrderBook  Trades  Fills  Positions │  │ │
│  │  WS Perps   │    │  └──────────────┬───────────────────────┘  │ │
│  └─────────────┘    │                 │                           │ │
│                     │  ┌──────────────▼───────────────────────┐  │ │
│  ┌─────────────┐    │  │            Strategies                │  │ │
│  │ Risk Layer  │    │  │                                      │  │ │
│  │             │    │  │  MarketMaker  ComplementArb          │  │ │
│  │ KillSwitch  │◀───│  │  CrossVenueHedge  CatalystTrader    │  │ │
│  │ Heartbeat   │    │  └──────────────┬───────────────────────┘  │ │
│  │ PositionLim │    │                 │                           │ │
│  └─────────────┘    │  ┌──────────────▼───────────────────────┐  │ │
│                     │  │         Order Routing                │  │ │
│  ┌─────────────┐    │  │                                      │  │ │
│  │  Data Store │    │  │  Python path   Rust hot path         │  │ │
│  │             │    │  │  (normal ops)  (cancel/replace)      │  │ │
│  │  Parquet    │◀───│  └──────────────┬───────────────────────┘  │ │
│  │  Catalog    │    └─────────────────┼───────────────────────────┘ │
│  └─────────────┘                      │                             │
│                                       │                             │
│                     ┌─────────────────▼───────────────────────┐    │
│                     │              Venues                      │    │
│                     │                                          │    │
│                     │   Polymarket CLOB    Hyperliquid Perps   │    │
│                     └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/your-org/Trading-Lab.git
cd Trading-Lab

# 2. Copy and configure environment
cp .env.example .env
# Edit .env with your API credentials (live trading needs real keys)

# 3. Preferred: run inside the NautilusTrader Docker image
docker compose up --build trader
# Or use the Makefile helpers:
make docker-build
make docker-up

# 4. Tail the container logs to confirm paper mode started
make docker-logs

# Optional (local dev): install deps and run paper trading directly
# make dev
# make paper
```

## Trading Venues

### Polymarket (Primary)
- Central Limit Order Book (CLOB) on Polygon
- Binary outcome prediction markets (YES/NO tokens)
- Zero maker fees + USDC rebates for passive orders
- EIP-712 L1 authentication → derived L2 API credentials
- WebSocket feeds for real-time order book and fills

### Hyperliquid (Price Oracle + Hedging)
- Perpetual futures exchange
- Used as price oracle for crypto event markets on Polymarket
- Direct hedging venue for cross-venue strategies

## Strategies

| Strategy | Description | Status |
|----------|-------------|--------|
| `PolymarketMarketMaker` | Quote both sides of CLOB, earn maker rebates | Stub |
| `ComplementArbStrategy` | YES + NO < 1.00 = risk-free arb | Stub |
| `CrossVenueHedgeStrategy` | Exploit Hyperliquid/Polymarket price discrepancies | Stub |
| `CatalystTrader` | 5-min crypto catalyst strategy using HL price feed | Stub |

## Development Phases

### Phase 0: Foundation (Current)
- Project scaffold and architecture
- Adapter interfaces and data types
- Risk module (kill switch, heartbeat, position limits)
- Test framework

### Phase 1: Data Infrastructure
- Historical trade and orderbook ingestion
- Parquet data catalog
- Replay tooling for backtesting

### Phase 2: Backtesting
- Complement arb backtest on historical data
- Market maker simulation with fee modeling
- Performance analytics

### Phase 3: Paper Trading
- Paper mode validation against live feeds
- Latency measurement and profiling
- Risk system integration testing

### Phase 4: Live Trading
- Production deployment with Docker
- Real-time monitoring and alerting
- Gradual position size ramp-up

## Documentation

- [Architecture](docs/architecture.md) - System design and component overview
- [Roadmap](docs/roadmap.md) - Implementation phases and milestones
- [Polymarket Auth](docs/polymarket_auth.md) - Authentication flow details

## Safety

This system has multiple layers of protection:
- **Kill Switch**: Halts all trading if daily loss limit is breached
- **Heartbeat Monitor**: Cancels all orders if connectivity is lost
- **Position Limits**: Per-market and total exposure caps
- **Live Trading Double Opt-In**: Requires both `TRADING_MODE=live` AND `LIVE_TRADING_CONFIRMED=true`

**Default mode is `paper` - live trading requires explicit configuration.**

## Requirements

- Python 3.12+
- Rust 1.75+ (for polyfill-rs)
- Docker (optional, for containerized deployment)

## License

MIT
