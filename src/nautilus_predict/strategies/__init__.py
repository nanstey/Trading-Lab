"""
Strategy implementations for Nautilus-Predict.

Available strategies:
- PolymarketMarketMaker: Quote both sides of the CLOB, earn maker rebates
- ComplementArbStrategy: Exploit YES+NO < 1.00 pricing discrepancies
- CrossVenueHedgeStrategy: Hedge Polymarket positions via Hyperliquid
- CatalystTrader: 5-minute crypto catalyst momentum strategy
"""
