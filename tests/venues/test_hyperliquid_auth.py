"""
Unit tests for Hyperliquid EIP-712 order signing.

All tests are offline — no network calls. Uses the same deterministic test
private key as the Polymarket auth tests.
"""

from __future__ import annotations

import time

import pytest

from nautilus_predict.venues.hyperliquid.auth import (
    current_nonce,
    derive_address,
    sign_l1_action,
)

TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

SAMPLE_ORDER_ACTION = {
    "type": "order",
    "orders": [
        {
            "a": 0,
            "b": True,
            "p": "30000.0",
            "s": "0.1",
            "r": False,
            "t": {"limit": {"tif": "Gtc"}},
        }
    ],
    "grouping": "na",
}


class TestDeriveAddress:
    def test_known_key(self) -> None:
        assert derive_address(TEST_PRIVATE_KEY).lower() == TEST_ADDRESS.lower()


class TestCurrentNonce:
    def test_returns_millisecond_timestamp(self) -> None:
        before = int(time.time() * 1000)
        nonce = current_nonce()
        after = int(time.time() * 1000)
        assert before <= nonce <= after + 1


class TestSignL1Action:
    def test_returns_hex_signature(self) -> None:
        nonce = 1700000000000
        sig = sign_l1_action(TEST_PRIVATE_KEY, SAMPLE_ORDER_ACTION, None, nonce)
        assert isinstance(sig, str)
        # EIP-712 signatures are 65 bytes = 130 hex chars
        assert len(sig) == 130

    def test_vault_address_changes_signature(self) -> None:
        nonce = 1700000000000
        vault = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
        sig_no_vault = sign_l1_action(TEST_PRIVATE_KEY, SAMPLE_ORDER_ACTION, None, nonce)
        sig_with_vault = sign_l1_action(TEST_PRIVATE_KEY, SAMPLE_ORDER_ACTION, vault, nonce)
        assert sig_no_vault != sig_with_vault

    def test_different_nonces_produce_different_signatures(self) -> None:
        sig1 = sign_l1_action(TEST_PRIVATE_KEY, SAMPLE_ORDER_ACTION, None, 1700000000000)
        sig2 = sign_l1_action(TEST_PRIVATE_KEY, SAMPLE_ORDER_ACTION, None, 1700000000001)
        assert sig1 != sig2

    def test_deterministic_for_same_inputs(self) -> None:
        nonce = 1700000000000
        sig1 = sign_l1_action(TEST_PRIVATE_KEY, SAMPLE_ORDER_ACTION, None, nonce)
        sig2 = sign_l1_action(TEST_PRIVATE_KEY, SAMPLE_ORDER_ACTION, None, nonce)
        assert sig1 == sig2
