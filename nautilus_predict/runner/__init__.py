"""
Execution runners for Nautilus-Predict.

Three runner modes:
- BacktestRunner: Replay historical Parquet data through NautilusTrader
- PaperRunner: Paper trading against live WebSocket feeds (no real orders)
- LiveRunner: Live trading with real order execution (requires double opt-in)
"""
