"""
Helpers for building NautilusTrader instrument descriptors for Hyperliquid.

HL listings are PERP swaps quoted/settled in USDC, linear (non-inverse).
For the smoke runner / paper-fill engine we keep precisions liberal and
defer real precision/tick lookup to a follow-up that consults `/info` meta.
"""

from __future__ import annotations

from nautilus_trader.model.currencies import USDC, USDT
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments.crypto_perpetual import CryptoPerpetual
from nautilus_trader.model.objects import Price, Quantity

HYPERLIQUID_VENUE = Venue("HYPERLIQUID")


def hl_instrument_id(coin: str) -> InstrumentId:
    """Stable instrument id for a coin (e.g. "BTC" → BTC-PERP.HYPERLIQUID)."""
    return InstrumentId(Symbol(f"{coin}-PERP"), HYPERLIQUID_VENUE)


def make_hl_perpetual(
    coin: str,
    *,
    price_precision: int = 2,
    size_precision: int = 6,
) -> CryptoPerpetual:
    """
    Build a generic `CryptoPerpetual` descriptor for an HL coin.

    HL settles all perps in USDC. Tick sizes vary by asset — we default
    to permissive values; a follow-up should fetch them from `/info meta`
    and cache per-asset.
    """
    symbol = f"{coin}-PERP"
    instrument_id = hl_instrument_id(coin)
    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=_currency_for(coin),
        quote_currency=USDC,
        settlement_currency=USDC,
        is_inverse=False,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=Price(10 ** -price_precision, price_precision),
        size_increment=Quantity(10 ** -size_precision, size_precision),
        ts_event=0,
        ts_init=0,
    )


def _currency_for(coin: str):
    """Best-effort base-currency lookup. Falls back to USDT as a placeholder."""
    from nautilus_trader.model.objects import Currency

    try:
        return Currency.from_str(coin.upper())
    except Exception:
        # base_currency is metadata only for paper purposes.
        return USDT
