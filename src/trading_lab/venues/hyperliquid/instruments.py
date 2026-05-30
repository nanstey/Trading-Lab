"""
Helpers for building NautilusTrader instrument descriptors for Hyperliquid.

HL listings are PERP swaps quoted/settled in USDC, linear (non-inverse).
For the smoke runner / paper-fill engine we keep precisions liberal and
defer real precision/tick lookup to a follow-up that consults `/info` meta.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.currencies import USDC, USDT
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments.crypto_perpetual import CryptoPerpetual
from nautilus_trader.model.objects import Price, Quantity

HYPERLIQUID_VENUE = Venue("HYPERLIQUID")

# Default HL public fee tier (bps). Override per-account if you qualify for
# better tiers — these are passed to `make_hl_perpetual` so the NT
# `MakerTakerFeeModel` charges the right commission in backtests.
DEFAULT_MAKER_BPS = 1.5
DEFAULT_TAKER_BPS = 4.5


def hl_instrument_id(coin: str) -> InstrumentId:
    """Stable instrument id for a coin (e.g. "BTC" → BTC-PERP.HYPERLIQUID)."""
    return InstrumentId(Symbol(f"{coin}-PERP"), HYPERLIQUID_VENUE)


def make_hl_perpetual(
    coin: str,
    *,
    price_precision: int = 2,
    size_precision: int = 6,
    maker_bps: float = DEFAULT_MAKER_BPS,
    taker_bps: float = DEFAULT_TAKER_BPS,
    margin_init_pct: float = 0.10,
    margin_maint_pct: float = 0.05,
) -> CryptoPerpetual:
    """
    Build a `CryptoPerpetual` descriptor for an HL coin.

    HL settles all perps in USDC. Fees and margin defaults match the public
    tier; pass overrides if your account qualifies for a better tier or you
    want to stress-test with worse assumptions.
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
        margin_init=Decimal(str(margin_init_pct)),
        margin_maint=Decimal(str(margin_maint_pct)),
        maker_fee=Decimal(str(maker_bps / 10_000)),
        taker_fee=Decimal(str(taker_bps / 10_000)),
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
