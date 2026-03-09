"""
Hyperliquid data types and domain models.

Canonical in-memory representations for Hyperliquid market data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class HyperliquidTicker:
    """
    Real-time price ticker for a Hyperliquid perpetual or spot market.

    Attributes
    ----------
    coin : str
        Coin symbol (e.g., "BTC", "ETH").
    mid_price : Decimal
        Current mid-price in USD.
    mark_price : Decimal
        Mark price used for funding rate calculations.
    open_interest : Decimal
        Total open interest in coin units.
    funding_rate : Decimal
        Current hourly funding rate.
    timestamp : int
        Unix timestamp (milliseconds) of this snapshot.
    """

    coin: str
    mid_price: Decimal
    mark_price: Decimal = Decimal("0")
    open_interest: Decimal = Decimal("0")
    funding_rate: Decimal = Decimal("0")
    timestamp: int = 0

    @classmethod
    def from_api(cls, coin: str, data: dict) -> HyperliquidTicker:
        """Parse a ticker from the Hyperliquid ctx response."""
        ctx = data.get("ctx", {})
        return cls(
            coin=coin,
            mid_price=Decimal(str(data.get("midPx", "0"))),
            mark_price=Decimal(str(ctx.get("markPx", "0"))),
            open_interest=Decimal(str(ctx.get("openInterest", "0"))),
            funding_rate=Decimal(str(ctx.get("funding", "0"))),
        )


@dataclass(slots=True)
class HyperliquidPriceLevel:
    """
    A single price level in a Hyperliquid order book.

    Attributes
    ----------
    price : Decimal
        Price in USD.
    size : Decimal
        Size at this price level in coin units.
    """

    price: Decimal
    size: Decimal

    @classmethod
    def from_api(cls, level: list) -> HyperliquidPriceLevel:
        """Parse from the Hyperliquid l2Book format [price, size, count]."""
        return cls(
            price=Decimal(str(level[0])),
            size=Decimal(str(level[1])),
        )


@dataclass(slots=True)
class HyperliquidOrderBook:
    """
    Order book snapshot for a Hyperliquid market.

    Attributes
    ----------
    coin : str
        Coin symbol.
    bids : list[HyperliquidPriceLevel]
        Bid levels sorted descending by price.
    asks : list[HyperliquidPriceLevel]
        Ask levels sorted ascending by price.
    timestamp : int
        Unix timestamp (milliseconds).
    """

    coin: str
    bids: list[HyperliquidPriceLevel] = field(default_factory=list)
    asks: list[HyperliquidPriceLevel] = field(default_factory=list)
    timestamp: int = 0

    @classmethod
    def from_api(cls, coin: str, data: dict) -> HyperliquidOrderBook:
        """Parse an order book from the Hyperliquid l2Book response."""
        levels = data.get("levels", [[], []])
        bids = [HyperliquidPriceLevel.from_api(l) for l in levels[0]] if len(levels) > 0 else []
        asks = [HyperliquidPriceLevel.from_api(l) for l in levels[1]] if len(levels) > 1 else []
        return cls(
            coin=coin,
            bids=bids,
            asks=asks,
            timestamp=int(data.get("time", 0)),
        )

    @property
    def best_bid(self) -> HyperliquidPriceLevel | None:
        """Return the best (highest) bid, or None if empty."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> HyperliquidPriceLevel | None:
        """Return the best (lowest) ask, or None if empty."""
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> Decimal | None:
        """Return the mid-price, or None if either side is empty."""
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid.price + self.best_ask.price) / Decimal("2")
