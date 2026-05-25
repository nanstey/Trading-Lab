"""
Strategy implementations for Nautilus-Predict.

Available strategies:
- BinaryArbStrategy: Exploit YES+NO < 1.00 complement arbitrage (canonical)
- PolymarketMarketMaker: Quote both sides of the CLOB, earn maker rebates
- CrossVenueHedgeStrategy: Hedge Polymarket positions via Hyperliquid
- CatalystTrader: 5-minute crypto catalyst momentum strategy
"""
