"""
Unit tests for PolymarketAuth.

Tests authentication logic without requiring real API credentials.
Uses a deterministic test private key for signature verification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

import pytest

from nautilus_predict.adapters.polymarket.auth import L2Credentials, PolymarketAuth


# Test private key (not a real funded wallet - DO NOT use for real trading)
# This is a well-known test key from the Ethereum test suite.
TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


class TestL2Credentials:
    """Test the L2Credentials dataclass."""

    def test_creation_with_all_fields(self) -> None:
        """L2Credentials should store all fields correctly."""
        creds = L2Credentials(
            api_key="test-api-key",
            api_secret="dGVzdC1zZWNyZXQ=",  # base64("test-secret")
            api_passphrase="test-passphrase",
        )
        assert creds.api_key == "test-api-key"
        assert creds.api_secret == "dGVzdC1zZWNyZXQ="
        assert creds.api_passphrase == "test-passphrase"

    def test_immutability(self) -> None:
        """L2Credentials should be frozen (immutable)."""
        creds = L2Credentials(
            api_key="key",
            api_secret="secret",
            api_passphrase="passphrase",
        )
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            creds.api_key = "new-key"  # type: ignore[misc]

    def test_equality(self) -> None:
        """Two L2Credentials with same values should be equal."""
        creds1 = L2Credentials(api_key="k", api_secret="s", api_passphrase="p")
        creds2 = L2Credentials(api_key="k", api_secret="s", api_passphrase="p")
        assert creds1 == creds2


class TestPolymarketAuth:
    """Test PolymarketAuth initialization and key management."""

    def test_init_with_0x_prefix(self) -> None:
        """Private key with 0x prefix should be accepted."""
        auth = PolymarketAuth(private_key=TEST_PRIVATE_KEY)
        assert auth.address == TEST_ADDRESS

    def test_init_without_0x_prefix(self) -> None:
        """Private key without 0x prefix should be accepted."""
        key_without_prefix = TEST_PRIVATE_KEY.removeprefix("0x")
        auth = PolymarketAuth(private_key=key_without_prefix)
        assert auth.address == TEST_ADDRESS

    def test_address_is_checksum(self) -> None:
        """Address should be in EIP-55 checksum format."""
        auth = PolymarketAuth(private_key=TEST_PRIVATE_KEY)
        # EIP-55 checksummed addresses have mixed case
        assert auth.address.startswith("0x")
        # Check it's a valid hex address (42 chars including 0x)
        assert len(auth.address) == 42

    def test_l2_credentials_initially_none(self) -> None:
        """L2 credentials should be None before set."""
        auth = PolymarketAuth(private_key=TEST_PRIVATE_KEY)
        assert auth.l2_credentials is None

    def test_set_l2_credentials(self) -> None:
        """set_l2_credentials should store and return credentials."""
        auth = PolymarketAuth(private_key=TEST_PRIVATE_KEY)
        creds = L2Credentials(api_key="k", api_secret="s", api_passphrase="p")
        auth.set_l2_credentials(creds)
        assert auth.l2_credentials == creds


class TestSignRequest:
    """Test HMAC-SHA256 request signing."""

    def _make_auth_with_creds(self) -> PolymarketAuth:
        """Create an auth instance with L2 credentials set."""
        # Use a base64-encoded test secret
        test_secret = base64.b64encode(b"test-hmac-secret").decode()
        auth = PolymarketAuth(private_key=TEST_PRIVATE_KEY)
        auth.set_l2_credentials(L2Credentials(
            api_key="test-api-key-uuid",
            api_secret=test_secret,
            api_passphrase="test-passphrase",
        ))
        return auth

    def test_sign_request_returns_dict(self) -> None:
        """sign_request should return a dict of headers."""
        auth = self._make_auth_with_creds()
        headers = auth.sign_request("GET", "/orders")
        assert isinstance(headers, dict)

    def test_sign_request_required_headers(self) -> None:
        """Signed request must include all required Polymarket headers."""
        auth = self._make_auth_with_creds()
        headers = auth.sign_request("GET", "/orders")

        required = {"POLY-ADDRESS", "POLY-SIGNATURE", "POLY-TIMESTAMP", "POLY-API-KEY", "POLY-PASSPHRASE"}
        assert required.issubset(headers.keys()), (
            f"Missing headers: {required - set(headers.keys())}"
        )

    def test_sign_request_api_key_in_header(self) -> None:
        """POLY-API-KEY header should match the configured api_key."""
        auth = self._make_auth_with_creds()
        headers = auth.sign_request("GET", "/orders")
        assert headers["POLY-API-KEY"] == "test-api-key-uuid"

    def test_sign_request_passphrase_in_header(self) -> None:
        """POLY-PASSPHRASE header should match the configured passphrase."""
        auth = self._make_auth_with_creds()
        headers = auth.sign_request("POST", "/order", '{"test": true}')
        assert headers["POLY-PASSPHRASE"] == "test-passphrase"

    def test_sign_request_timestamp_is_numeric(self) -> None:
        """POLY-TIMESTAMP should be a numeric string."""
        auth = self._make_auth_with_creds()
        headers = auth.sign_request("GET", "/markets")
        assert headers["POLY-TIMESTAMP"].isdigit()

    def test_sign_request_timestamp_is_recent(self) -> None:
        """POLY-TIMESTAMP should be within a few seconds of current time."""
        auth = self._make_auth_with_creds()
        before = int(time.time()) - 2
        headers = auth.sign_request("GET", "/markets")
        after = int(time.time()) + 2
        ts = int(headers["POLY-TIMESTAMP"])
        assert before <= ts <= after, f"Timestamp {ts} not in range [{before}, {after}]"

    def test_sign_request_hmac_is_valid(self) -> None:
        """Verify HMAC-SHA256 signature is correct."""
        test_secret_bytes = b"test-hmac-secret"
        test_secret_b64 = base64.b64encode(test_secret_bytes).decode()

        auth = PolymarketAuth(private_key=TEST_PRIVATE_KEY)
        auth.set_l2_credentials(L2Credentials(
            api_key="test-key",
            api_secret=test_secret_b64,
            api_passphrase="test-pass",
        ))

        method = "POST"
        path = "/order"
        body = '{"token_id":"0xabc","side":"BUY"}'

        headers = auth.sign_request(method, path, body)
        timestamp = headers["POLY-TIMESTAMP"]

        # Reconstruct expected signature
        message = timestamp + method.upper() + path + body
        expected_sig_bytes = hmac.new(test_secret_bytes, message.encode("utf-8"), hashlib.sha256).digest()
        expected_sig = base64.b64encode(expected_sig_bytes).decode()

        assert headers["POLY-SIGNATURE"] == expected_sig, (
            "HMAC-SHA256 signature does not match expected value"
        )

    def test_sign_request_without_creds_raises(self) -> None:
        """sign_request without L2 credentials should raise RuntimeError."""
        auth = PolymarketAuth(private_key=TEST_PRIVATE_KEY)
        with pytest.raises(RuntimeError, match="L2 credentials not set"):
            auth.sign_request("GET", "/orders")

    def test_sign_get_vs_post_differ(self) -> None:
        """GET and POST signatures for same path should differ (method is in the message)."""
        auth = self._make_auth_with_creds()
        headers_get = auth.sign_request("GET", "/orders")
        headers_post = auth.sign_request("POST", "/orders")
        assert headers_get["POLY-SIGNATURE"] != headers_post["POLY-SIGNATURE"]

    def test_sign_with_body_vs_empty_differ(self) -> None:
        """Signatures with vs without body should differ."""
        auth = self._make_auth_with_creds()
        headers_empty = auth.sign_request("POST", "/order", "")
        headers_body = auth.sign_request("POST", "/order", '{"test":1}')
        assert headers_empty["POLY-SIGNATURE"] != headers_body["POLY-SIGNATURE"]
