"""
Polymarket Two-Level Authentication.

Polymarket uses a two-level auth system:

Level 1 (L1) - On-chain:
    Your Ethereum/Polygon private key signs an EIP-712 typed message.
    This proves you control the wallet holding the USDC collateral.
    Used ONCE to derive L2 credentials (or to register pre-generated ones).

Level 2 (L2) - Off-chain API:
    Derived API credentials (api_key, api_secret, api_passphrase).
    Used for all CLOB REST and WebSocket requests.
    Each REST request is signed with HMAC-SHA256.

Auth flow:
    1. Sign EIP-712 message with L1 private key
    2. POST to /auth/derive-api-key with L1 signature headers
    3. Store returned L2 credentials securely (in .env)
    4. Use L2 credentials + HMAC-SHA256 for all subsequent requests

Reference: https://docs.polymarket.com/#authentication
"""

from __future__ import annotations

import hashlib
import hmac
import time
from base64 import b64decode, b64encode
from dataclasses import dataclass

from eth_account import Account
from eth_account.messages import encode_typed_data


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class L2Credentials:
    """
    Polymarket L2 API credentials.

    These are derived from the L1 private key via /auth/derive-api-key
    and should be stored in environment variables for subsequent runs.
    """

    api_key: str
    """L2 API key (UUID format)."""

    api_secret: str
    """Base64-encoded HMAC secret."""

    api_passphrase: str
    """Passphrase for request signing."""


# ---------------------------------------------------------------------------
# EIP-712 domain and types for Polymarket L1 auth
# ---------------------------------------------------------------------------

_CLOB_AUTH_DOMAIN = {
    "name": "ClobAuthDomain",
    "version": "1",
    "chainId": 137,  # Polygon mainnet
}

_CLOB_AUTH_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "ClobAuth": [
        {"name": "address", "type": "address"},
        {"name": "timestamp", "type": "string"},
        {"name": "nonce", "type": "int256"},
        {"name": "message", "type": "string"},
    ],
}

_ATTESTATION_MESSAGE = "This message attests that I control the given wallet"


class PolymarketAuth:
    """
    Polymarket authentication handler.

    Manages both L1 (EIP-712 wallet signing) and L2 (HMAC-SHA256 API)
    authentication for the Polymarket CLOB.

    Parameters
    ----------
    private_key : str
        Ethereum private key (hex string, with or without 0x prefix).
        Used for L1 EIP-712 signing and to derive the wallet address.

    Example
    -------
    >>> auth = PolymarketAuth(private_key="your_key_here")
    >>> creds = await auth.derive_l2_credentials()
    >>> headers = auth.sign_request("GET", "/orders", "")
    """

    def __init__(self, private_key: str) -> None:
        """
        Initialize with L1 private key.

        Parameters
        ----------
        private_key : str
            Ethereum private key in hex format (with or without 0x prefix).
        """
        # Normalize key format
        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"
        self._private_key = private_key
        self._account = Account.from_key(private_key)
        self._address = self._account.address
        self._l2_creds: L2Credentials | None = None

    @property
    def address(self) -> str:
        """Return the Ethereum address for this private key."""
        return self._address

    @property
    def l2_credentials(self) -> L2Credentials | None:
        """Return L2 credentials if they have been set or derived."""
        return self._l2_creds

    def set_l2_credentials(self, creds: L2Credentials) -> None:
        """
        Set L2 credentials directly (e.g., loaded from environment).

        Use this when credentials are pre-stored in environment variables
        to avoid calling the derive endpoint on every startup.

        Parameters
        ----------
        creds : L2Credentials
            Pre-configured L2 API credentials.
        """
        self._l2_creds = creds

    def sign_l1_message(self, timestamp: str, nonce: int = 0) -> str:
        """
        Sign an EIP-712 message with the L1 private key.

        This produces the signature used to derive L2 credentials.

        Parameters
        ----------
        timestamp : str
            Unix timestamp as string (seconds since epoch).
        nonce : int
            Credential nonce. Increment to rotate/invalidate old credentials.

        Returns
        -------
        str
            Hex-encoded EIP-712 signature (0x-prefixed).
        """
        structured_data = {
            "types": _CLOB_AUTH_TYPES,
            "domain": _CLOB_AUTH_DOMAIN,
            "primaryType": "ClobAuth",
            "message": {
                "address": self._address,
                "timestamp": timestamp,
                "nonce": nonce,
                "message": _ATTESTATION_MESSAGE,
            },
        }
        signable = encode_typed_data(full_message=structured_data)
        signed = self._account.sign_message(signable)
        return "0x" + signed.signature.hex()

    async def derive_l2_credentials(
        self,
        host: str = "https://clob.polymarket.com",
        nonce: int = 0,
    ) -> L2Credentials:
        """
        Derive L2 API credentials from the L1 private key via Polymarket API.

        Calls POST /auth/derive-api-key with an EIP-712 signature.
        Credentials should be stored after first call and reused.

        Parameters
        ----------
        host : str
            Polymarket CLOB API base URL.
        nonce : int
            Nonce for credential derivation. Increment to rotate credentials.

        Returns
        -------
        L2Credentials
            Freshly derived L2 API credentials.

        TODO(live): Wire to httpx.AsyncClient (currently uses aiohttp stub)
        TODO(live): Handle API errors (rate limits, invalid signature, etc.)
        """
        import httpx

        timestamp = str(int(time.time()))
        signature = self.sign_l1_message(timestamp, nonce)

        headers = {
            "POLY-ADDRESS": self._address,
            "POLY-SIGNATURE": signature,
            "POLY-TIMESTAMP": timestamp,
            "POLY-NONCE": str(nonce),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{host}/auth/derive-api-key", headers=headers)
            resp.raise_for_status()
            data = resp.json()

        creds = L2Credentials(
            api_key=data["apiKey"],
            api_secret=data["secret"],
            api_passphrase=data["passphrase"],
        )
        self._l2_creds = creds
        return creds

    def sign_request(
        self,
        method: str,
        path: str,
        body: str = "",
    ) -> dict[str, str]:
        """
        Generate HMAC-SHA256 signed headers for a Polymarket L2 API request.

        The HMAC signature covers: timestamp + METHOD + path + body.
        The api_secret is base64-decoded before use as the HMAC key.

        Parameters
        ----------
        method : str
            HTTP method in uppercase (e.g., "GET", "POST", "DELETE").
        path : str
            Request path including query string (e.g., "/orders?market=0xabc").
        body : str
            JSON-encoded request body. Use empty string for GET requests.

        Returns
        -------
        dict[str, str]
            Dictionary of signed headers to attach to the HTTP request.

        Raises
        ------
        RuntimeError
            If L2 credentials have not been set. Call derive_l2_credentials()
            or set_l2_credentials() first.
        """
        if self._l2_creds is None:
            raise RuntimeError(
                "L2 credentials not set. Call derive_l2_credentials() or "
                "set_l2_credentials() before signing requests."
            )

        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body

        secret_bytes = b64decode(self._l2_creds.api_secret)
        sig_bytes = hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256).digest()
        signature = b64encode(sig_bytes).decode("utf-8")

        return {
            "POLY-ADDRESS": self._address,
            "POLY-SIGNATURE": signature,
            "POLY-TIMESTAMP": timestamp,
            "POLY-API-KEY": self._l2_creds.api_key,
            "POLY-PASSPHRASE": self._l2_creds.api_passphrase,
        }
