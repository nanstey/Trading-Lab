"""
Unit tests for Polymarket authentication (L1 EIP-712 + L2 HMAC-SHA256).

These tests run offline with no network calls. Keys are deterministic test
vectors — never real credentials.
"""

from __future__ import annotations

import time
from base64 import b64encode
from unittest.mock import patch

from trading_lab.venues.polymarket.auth import (
    L2Credentials,
    derive_address,
    sign_eip712_message,
    sign_l2_request,
)

# Deterministic test private key (NOT a real key — do not fund)
TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
# Known address for the above key
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


class TestDeriveAddress:
    def test_known_key_produces_known_address(self) -> None:
        assert derive_address(TEST_PRIVATE_KEY).lower() == TEST_ADDRESS.lower()

    def test_key_without_prefix_works(self) -> None:
        key_no_prefix = TEST_PRIVATE_KEY.removeprefix("0x")
        assert derive_address(key_no_prefix).lower() == TEST_ADDRESS.lower()


class TestSignEip712:
    def test_returns_hex_string(self) -> None:
        ts = str(int(time.time()))
        sig = sign_eip712_message(TEST_PRIVATE_KEY, TEST_ADDRESS, ts)
        assert isinstance(sig, str)
        assert sig.startswith("0x")  # Polymarket requires 0x-prefixed signature
        assert len(sig) == 132  # 0x + 65 bytes → 132 chars

    def test_different_timestamps_produce_different_signatures(self) -> None:
        sig1 = sign_eip712_message(TEST_PRIVATE_KEY, TEST_ADDRESS, "1700000000")
        sig2 = sign_eip712_message(TEST_PRIVATE_KEY, TEST_ADDRESS, "1700000001")
        assert sig1 != sig2

    def test_different_nonces_produce_different_signatures(self) -> None:
        ts = "1700000000"
        sig1 = sign_eip712_message(TEST_PRIVATE_KEY, TEST_ADDRESS, ts, nonce=0)
        sig2 = sign_eip712_message(TEST_PRIVATE_KEY, TEST_ADDRESS, ts, nonce=1)
        assert sig1 != sig2


class TestSignL2Request:
    def _make_creds(self) -> L2Credentials:
        # 32-byte secret, base64-encoded
        secret_bytes = b"\xde\xad\xbe\xef" * 8
        return L2Credentials(
            api_key="test-api-key",
            api_secret=b64encode(secret_bytes).decode(),
            api_passphrase="test-passphrase",
        )

    def test_returns_all_required_headers(self) -> None:
        creds = self._make_creds()
        headers = sign_l2_request(creds, "GET", "/orders").as_dict()

        assert "POLY-ADDRESS" in headers
        assert "POLY-SIGNATURE" in headers
        assert "POLY-TIMESTAMP" in headers
        assert "POLY-API-KEY" in headers
        assert "POLY-PASSPHRASE" in headers

    def test_timestamp_is_recent(self) -> None:
        creds = self._make_creds()
        headers = sign_l2_request(creds, "GET", "/orders").as_dict()
        ts = int(headers["POLY-TIMESTAMP"])
        assert abs(ts - int(time.time())) <= 5

    def test_signature_differs_by_method(self) -> None:
        creds = self._make_creds()
        # Use fixed timestamp to isolate the method-dependency of the signature
        with patch("trading_lab.venues.polymarket.auth.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            sig_get = sign_l2_request(creds, "GET", "/orders").as_dict()["POLY-SIGNATURE"]
            sig_post = sign_l2_request(creds, "POST", "/orders").as_dict()["POLY-SIGNATURE"]
        assert sig_get != sig_post

    def test_body_affects_signature(self) -> None:
        creds = self._make_creds()
        with patch("trading_lab.venues.polymarket.auth.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            sig_empty = sign_l2_request(creds, "POST", "/order", "").as_dict()["POLY-SIGNATURE"]
            sig_body = sign_l2_request(creds, "POST", "/order", '{"test":1}').as_dict()["POLY-SIGNATURE"]
        assert sig_empty != sig_body

    def test_uses_explicit_address_when_present(self) -> None:
        creds = L2Credentials(
            api_key="test-api-key",
            api_secret=self._make_creds().api_secret,
            api_passphrase="test-passphrase",
            address=TEST_ADDRESS,
        )
        headers = sign_l2_request(creds, "GET", "/orders").as_dict()
        assert headers["POLY-ADDRESS"] == TEST_ADDRESS

    def test_signature_is_urlsafe_base64(self) -> None:
        creds = self._make_creds()
        with patch("trading_lab.venues.polymarket.auth.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            sig = sign_l2_request(creds, "GET", "/orders").as_dict()["POLY-SIGNATURE"]
        assert "+" not in sig
        assert "/" not in sig
