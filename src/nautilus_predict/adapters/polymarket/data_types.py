"""
Polymarket data types and domain models.

Defines the canonical in-memory representations for all Polymarket
market data. These types are used throughout the adapter layer and
strategy logic, independent of the raw API response format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class OrderSide(StrEnum):
    """Order side for CLOB orders."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    """Order lifecycle status."""

    OPEN = "OPEN"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"


class MarketOutcome(StrEnum):
    """Binary market outcomes."""

    YES = "YES"
    NO = "NO"


@dataclass(frozen=True, slots=True)
class PriceLevel:
    """
    A single price level in an order book.

    Attributes
    ----------
    price : Decimal
        Price in USDC (0.00 to 1.00 for binary markets).
    size : Decimal
        Available size in outcome tokens.
    """

    price: Decimal
    size: Decimal

    @classmethod
    def from_api(cls, data: dict[str, str]) -> PriceLevel:
        """Parse a price level from the Polymarket API response format."""
        return cls(
            price=Decimal(data["price"]),
            size=Decimal(data["size"]),
        )


@dataclass(slots=True)
class PolymarketMarket:
    """
    Polymarket binary outcome market.

    Represents one 'condition' which resolves to exactly one outcome.
    Each market has exactly two outcome tokens: YES and NO.

    Attributes
    ----------
    condition_id : str
        Unique market identifier (0x-prefixed hex, 32 bytes).
    question : str
        Human-readable market question.
    yes_token_id : str
        Token ID for the YES outcome.
    no_token_id : str
        Token ID for the NO outcome.
    active : bool
        Whether the market is currently accepting orders.
    closed : bool
        Whether the market has resolved.
    end_date_iso : str
        ISO-8601 resolution date.
    """

    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    active: bool = True
    closed: bool = False
    end_date_iso: str = ""

    @classmethod
    def from_api(cls, data: dict) -> PolymarketMarket:
        """Parse a market from the Polymarket API response."""
        tokens = data.get("tokens", [])
        yes_token = next((t["token_id"] for t in tokens if t.get("outcome") == "Yes"), "")
        no_token = next((t["token_id"] for t in tokens if t.get("outcome") == "No"), "")

        return cls(
            condition_id=data["condition_id"],
            question=data.get("question", ""),
            yes_token_id=yes_token,
            no_token_id=no_token,
            active=data.get("active", True),
            closed=data.get("closed", False),
            end_date_iso=data.get("end_date_iso", ""),
        )


@dataclass(slots=True)
class PolymarketOrderBook:
    """
    Snapshot of the order book for a single outcome token.

    Attributes
    ----------
    token_id : str
        Outcome token ID this book belongs to.
    bids : list[PriceLevel]
        Bid side of the book, sorted descending by price.
    asks : list[PriceLevel]
        Ask side of the book, sorted ascending by price.
    timestamp : int
        Unix timestamp (milliseconds) of the snapshot.
    """

    token_id: str
    bids: list[PriceLevel] = field(default_factory=list)
    asks: list[PriceLevel] = field(default_factory=list)
    timestamp: int = 0

    @classmethod
    def from_api(cls, data: dict) -> PolymarketOrderBook:
        """Parse an order book snapshot from the Polymarket API response."""
        return cls(
            token_id=data.get("asset_id", ""),
            bids=[PriceLevel.from_api(b) for b in data.get("bids", [])],
            asks=[PriceLevel.from_api(a) for a in data.get("asks", [])],
            timestamp=int(data.get("timestamp", 0)),
        )

    @property
    def best_bid(self) -> PriceLevel | None:
        """Return the best (highest) bid price level, or None if empty."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> PriceLevel | None:
        """Return the best (lowest) ask price level, or None if empty."""
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> Decimal | None:
        """Return the mid-price, or None if either side is empty."""
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid.price + self.best_ask.price) / Decimal("2")

    @property
    def spread(self) -> Decimal | None:
        """Return the bid-ask spread, or None if either side is empty."""
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask.price - self.best_bid.price


@dataclass(slots=True)
class PolymarketOrder:
    """
    A Polymarket CLOB order.

    Attributes
    ----------
    order_id : str
        Unique order identifier.
    token_id : str
        Outcome token this order is for.
    side : OrderSide
        BUY or SELL.
    price : Decimal
        Limit price in USDC (0.01 to 0.99).
    size : Decimal
        Order size in outcome tokens.
    size_matched : Decimal
        Amount filled so far.
    status : OrderStatus
        Current order lifecycle status.
    fee_rate_bps : int
        Fee rate in basis points for this order.
    """

    order_id: str
    token_id: str
    side: OrderSide
    price: Decimal
    size: Decimal
    size_matched: Decimal = Decimal("0")
    status: OrderStatus = OrderStatus.OPEN
    fee_rate_bps: int = 0

    @classmethod
    def from_api(cls, data: dict) -> PolymarketOrder:
        """Parse an order from the Polymarket API response."""
        return cls(
            order_id=data["id"],
            token_id=data.get("asset_id", ""),
            side=OrderSide(data.get("side", "BUY").upper()),
            price=Decimal(data.get("price", "0")),
            size=Decimal(data.get("original_size", "0")),
            size_matched=Decimal(data.get("size_matched", "0")),
            status=OrderStatus(data.get("status", "OPEN").upper()),
            fee_rate_bps=int(data.get("feeRateBps", 0)),
        )

    @property
    def size_remaining(self) -> Decimal:
        """Return the unfilled order size."""
        return self.size - self.size_matched

    @property
    def is_open(self) -> bool:
        """Return True if the order is still open."""
        return self.status == OrderStatus.OPEN


@dataclass(frozen=True, slots=True)
class PolymarketFill:
    """
    A completed order fill on Polymarket.

    Attributes
    ----------
    fill_id : str
        Unique fill/trade identifier.
    order_id : str
        The order that was (partially) filled.
    token_id : str
        Outcome token that was traded.
    side : OrderSide
        Whether this was a buy or sell.
    price : Decimal
        Fill price in USDC.
    size : Decimal
        Fill size in outcome tokens.
    fee : Decimal
        Fee charged (negative for maker rebates).
    timestamp : int
        Unix timestamp (milliseconds) of the fill.
    """

    fill_id: str
    order_id: str
    token_id: str
    side: OrderSide
    price: Decimal
    size: Decimal
    fee: Decimal
    timestamp: int

    @classmethod
    def from_api(cls, data: dict) -> PolymarketFill:
        """Parse a fill event from the Polymarket API response."""
        return cls(
            fill_id=data.get("trade_id", ""),
            order_id=data.get("order_id", ""),
            token_id=data.get("asset_id", ""),
            side=OrderSide(data.get("side", "BUY").upper()),
            price=Decimal(data.get("price", "0")),
            size=Decimal(data.get("size", "0")),
            fee=Decimal(data.get("fee_rate_bps", "0")),
            timestamp=int(data.get("timestamp", 0)),
        )
