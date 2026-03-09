"""
Polymarket order construction and EIP-712 signing.

Orders on Polymarket must be signed with the wallet private key before
submission. The signing follows the EIP-712 standard using the CTF Exchange
contract's domain and Order struct types.

Reference: https://docs.polymarket.com/#placing-orders
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data


class Side(IntEnum):
    BUY = 0
    SELL = 1


class OrderType(IntEnum):
    LIMIT = 0
    MARKET = 1


# Polymarket uses 1e6 fixed-point for prices (USDC has 6 decimals on Polygon)
PRICE_DECIMALS = 6
SIZE_DECIMALS = 6

_EXCHANGE_DOMAIN = {
    "name": "Exchange",
    "version": "1",
    "chainId": 137,
}

_ORDER_TYPES = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}


@dataclass
class SignedOrder:
    """A fully signed Polymarket limit order ready for submission."""

    salt: int
    maker: str
    signer: str
    taker: str
    token_id: str
    maker_amount: int
    taker_amount: int
    expiration: int
    nonce: int
    fee_rate_bps: int
    side: Side
    signature_type: int
    signature: str

    def to_api_payload(self) -> dict[str, Any]:
        """Serialise to the JSON payload expected by POST /order."""
        return {
            "salt": str(self.salt),
            "maker": self.maker,
            "signer": self.signer,
            "taker": self.taker,
            "tokenId": self.token_id,
            "makerAmount": str(self.maker_amount),
            "takerAmount": str(self.taker_amount),
            "expiration": str(self.expiration),
            "nonce": str(self.nonce),
            "feeRateBps": str(self.fee_rate_bps),
            "side": self.side.value,
            "signatureType": self.signature_type,
            "signature": self.signature,
        }


def build_limit_order(
    private_key: str,
    token_id: str,
    side: Side,
    price: float,
    size: float,
    exchange_address: str,
    expiration: int = 0,
    nonce: int = 0,
    fee_rate_bps: int = 0,
    taker: str = "0x0000000000000000000000000000000000000000",
) -> SignedOrder:
    """
    Construct and sign a Polymarket limit order.

    Parameters
    ----------
    private_key : str
        Ethereum private key of the maker wallet.
    token_id : str
        ERC-1155 token ID representing the YES or NO share.
    side : Side
        BUY or SELL.
    price : float
        Limit price in USDC (e.g. 0.55 for $0.55 per share).
    size : float
        Order size in shares.
    exchange_address : str
        Polymarket CTF Exchange contract address on Polygon.
    expiration : int
        Unix timestamp after which the order expires (0 = GTC).
    nonce : int
        Maker account nonce for replay protection.
    fee_rate_bps : int
        Taker fee in basis points (typically 0 for makers).
    taker : str
        Counterparty address (zero address = open to anyone).

    Returns
    -------
    SignedOrder
        Signed order ready for submission.
    """
    account = Account.from_key(private_key)
    maker_address = account.address

    # Convert to fixed-point integers
    price_int = int(round(price * 10**PRICE_DECIMALS))
    size_int = int(round(size * 10**SIZE_DECIMALS))

    if side == Side.BUY:
        # Maker pays USDC (makerAmount), receives shares (takerAmount)
        maker_amount = int(price_int * size_int // 10**SIZE_DECIMALS)
        taker_amount = size_int
    else:
        # Maker pays shares (makerAmount), receives USDC (takerAmount)
        maker_amount = size_int
        taker_amount = int(price_int * size_int // 10**SIZE_DECIMALS)

    import random
    salt = random.randint(1, 2**256 - 1)

    order_struct = {
        "salt": salt,
        "maker": maker_address,
        "signer": maker_address,
        "taker": taker,
        "tokenId": int(token_id),
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": expiration,
        "nonce": nonce,
        "feeRateBps": fee_rate_bps,
        "side": side.value,
        "signatureType": 0,  # EOA signature
    }

    structured_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            **_ORDER_TYPES,
        },
        "domain": {**_EXCHANGE_DOMAIN, "verifyingContract": exchange_address},
        "primaryType": "Order",
        "message": order_struct,
    }

    signable = encode_typed_data(full_message=structured_data)
    signed = Account.sign_message(signable, private_key=private_key)

    return SignedOrder(
        salt=salt,
        maker=maker_address,
        signer=maker_address,
        taker=taker,
        token_id=token_id,
        maker_amount=maker_amount,
        taker_amount=taker_amount,
        expiration=expiration,
        nonce=nonce,
        fee_rate_bps=fee_rate_bps,
        side=side,
        signature_type=0,
        signature=signed.signature.hex(),
    )
