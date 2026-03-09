"""
Unit tests for Polymarket order construction and EIP-712 signing.
"""

from __future__ import annotations

import pytest

from nautilus_predict.venues.polymarket.orders import (
    Side,
    SignedOrder,
    build_limit_order,
)

TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
TEST_TOKEN_ID = "71321045679252212594626385532706912750332728571942532289631379312455583992563"
TEST_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"


class TestBuildLimitOrder:
    def test_buy_order_structure(self) -> None:
        order = build_limit_order(
            private_key=TEST_PRIVATE_KEY,
            token_id=TEST_TOKEN_ID,
            side=Side.BUY,
            price=0.55,
            size=100.0,
            exchange_address=TEST_EXCHANGE,
        )
        assert isinstance(order, SignedOrder)
        assert order.side == Side.BUY
        assert order.maker.lower() == TEST_ADDRESS.lower()
        assert order.signature_type == 0
        assert len(order.signature) == 130  # 65 bytes hex

    def test_sell_order_structure(self) -> None:
        order = build_limit_order(
            private_key=TEST_PRIVATE_KEY,
            token_id=TEST_TOKEN_ID,
            side=Side.SELL,
            price=0.60,
            size=50.0,
            exchange_address=TEST_EXCHANGE,
        )
        assert order.side == Side.SELL
        # For a sell: makerAmount = shares, takerAmount = USDC received
        assert order.maker_amount > 0
        assert order.taker_amount > 0

    def test_buy_amounts_correct(self) -> None:
        price = 0.55
        size = 100.0
        order = build_limit_order(
            private_key=TEST_PRIVATE_KEY,
            token_id=TEST_TOKEN_ID,
            side=Side.BUY,
            price=price,
            size=size,
            exchange_address=TEST_EXCHANGE,
        )
        # makerAmount = USDC paid = price * size in fixed-point (1e6)
        expected_usdc = int(price * 1e6) * int(size * 1e6) // int(1e6)
        assert order.maker_amount == expected_usdc
        assert order.taker_amount == int(size * 1e6)

    def test_api_payload_keys(self) -> None:
        order = build_limit_order(
            private_key=TEST_PRIVATE_KEY,
            token_id=TEST_TOKEN_ID,
            side=Side.BUY,
            price=0.55,
            size=10.0,
            exchange_address=TEST_EXCHANGE,
        )
        payload = order.to_api_payload()
        required_keys = {
            "salt", "maker", "signer", "taker", "tokenId",
            "makerAmount", "takerAmount", "expiration", "nonce",
            "feeRateBps", "side", "signatureType", "signature",
        }
        assert required_keys == set(payload.keys())

    def test_different_salts_per_call(self) -> None:
        """Each call should produce a unique salt for replay protection."""
        orders = [
            build_limit_order(
                private_key=TEST_PRIVATE_KEY,
                token_id=TEST_TOKEN_ID,
                side=Side.BUY,
                price=0.55,
                size=10.0,
                exchange_address=TEST_EXCHANGE,
            )
            for _ in range(5)
        ]
        salts = {o.salt for o in orders}
        # Should be all unique (extremely unlikely to collide)
        assert len(salts) == 5
