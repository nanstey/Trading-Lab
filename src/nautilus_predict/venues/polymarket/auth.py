"""
Polymarket two-level authentication.

L1 (on-chain): An Ethereum EOA private key signs an EIP-712 typed message to
prove ownership of funds. This is used once to derive or register L2 API creds.

L2 (off-chain): HMAC-SHA256 signed REST requests and WebSocket subscriptions
using the API key/secret/passphrase returned by /auth/derive-api-key.

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
    """Polymarket L2 API credentials."""

    api_key: str
    api_secret: str        # base64-encoded HMAC secret
    api_passphrase: str


@dataclass(frozen=True, slots=True)
class L2Headers:
    """Signed request headers for a Polymarket API call."""

    POLY_ADDRESS: str
    POLY_SIGNATURE: str
    POLY_TIMESTAMP: str
    POLY_API_KEY: str
    POLY_PASSPHRASE: str

    def as_dict(self) -> dict[str, str]:
        return {
            "POLY-ADDRESS": self.POLY_ADDRESS,
            "POLY-SIGNATURE": self.POLY_SIGNATURE,
            "POLY-TIMESTAMP": self.POLY_TIMESTAMP,
            "POLY-API-KEY": self.POLY_API_KEY,
            "POLY-PASSPHRASE": self.POLY_PASSPHRASE,
        }


# ---------------------------------------------------------------------------
# EIP-712 domain and types for Polymarket
# ---------------------------------------------------------------------------

_CLOB_DOMAIN = {
    "name": "ClobAuthDomain",
    "version": "1",
    "chainId": 137,  # Polygon mainnet
}

_CLOB_TYPES = {
    "ClobAuth": [
        {"name": "address", "type": "address"},
        {"name": "timestamp", "type": "string"},
        {"name": "nonce", "type": "int256"},
        {"name": "message", "type": "string"},
    ]
}


# ---------------------------------------------------------------------------
# L1: EIP-712 signing
# ---------------------------------------------------------------------------


def sign_eip712_message(
    private_key: str,
    address: str,
    timestamp: str,
    nonce: int = 0,
    message: str = "This message attests that I control the given wallet",
) -> str:
    """
    Produce an EIP-712 signature for Polymarket L1 authentication.

    Parameters
    ----------
    private_key : str
        Hex-encoded Ethereum private key (with or without 0x prefix).
    address : str
        Ethereum checksummed address corresponding to private_key.
    timestamp : str
        Unix timestamp as string (seconds since epoch).
    nonce : int
        Account nonce (0 for read-only / credential derivation).
    message : str
        Human-readable attestation string.

    Returns
    -------
    str
        Hex-encoded signature (0x-prefixed).
    """
    structured_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            **_CLOB_TYPES,
        },
        "domain": _CLOB_DOMAIN,
        "primaryType": "ClobAuth",
        "message": {
            "address": address,
            "timestamp": timestamp,
            "nonce": nonce,
            "message": message,
        },
    }

    signable = encode_typed_data(full_message=structured_data)
    signed = Account.sign_message(signable, private_key=private_key)
    return signed.signature.hex()


def derive_address(private_key: str) -> str:
    """Return the Ethereum address corresponding to a private key."""
    account = Account.from_key(private_key)
    return account.address


# ---------------------------------------------------------------------------
# L2: HMAC-SHA256 request signing
# ---------------------------------------------------------------------------


def sign_l2_request(
    creds: L2Credentials,
    method: str,
    path: str,
    body: str = "",
) -> L2Headers:
    """
    Generate signed headers for a Polymarket CLOB API request.

    The signature covers: timestamp + method (uppercase) + path + body.
    The secret is base64-decoded before use.

    Parameters
    ----------
    creds : L2Credentials
        Polymarket L2 API credentials.
    method : str
        HTTP method (GET, POST, DELETE …).
    path : str
        Request path including query string (e.g. "/orders?market=0xabc").
    body : str
        JSON-encoded request body (empty string for GET requests).

    Returns
    -------
    L2Headers
        Headers dict ready to attach to the HTTP request.
    """
    ts = str(int(time.time()))
    message = ts + method.upper() + path + body

    secret_bytes = b64decode(creds.api_secret)
    sig_bytes = hmac.new(secret_bytes, message.encode(), hashlib.sha256).digest()
    signature = b64encode(sig_bytes).decode()

    # We need the wallet address associated with the L2 key.
    # The server correlates api_key → address on its side.
    # We send api_key as the address identifier here.
    return L2Headers(
        POLY_ADDRESS=creds.api_key,   # api_key IS the identifier address
        POLY_SIGNATURE=signature,
        POLY_TIMESTAMP=ts,
        POLY_API_KEY=creds.api_key,
        POLY_PASSPHRASE=creds.api_passphrase,
    )


# ---------------------------------------------------------------------------
# Credential derivation helper
# ---------------------------------------------------------------------------


async def derive_api_key(
    http_url: str,
    private_key: str,
    nonce: int = 0,
) -> L2Credentials:
    """
    Call /auth/derive-api-key to obtain L2 credentials from an L1 signature.

    This should be called once and the returned credentials stored securely.
    Subsequent runs should use stored credentials via environment variables.

    Parameters
    ----------
    http_url : str
        Base CLOB API URL (e.g. "https://clob.polymarket.com").
    private_key : str
        Ethereum private key for the funding wallet.
    nonce : int
        Nonce to use; increment to rotate credentials.

    Returns
    -------
    L2Credentials
        Ready-to-use API credentials.
    """
    import aiohttp

    address = derive_address(private_key)
    timestamp = str(int(time.time()))
    signature = sign_eip712_message(private_key, address, timestamp, nonce)

    headers = {
        "POLY-ADDRESS": address,
        "POLY-SIGNATURE": signature,
        "POLY-TIMESTAMP": timestamp,
        "POLY-NONCE": str(nonce),
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{http_url}/auth/derive-api-key",
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

    return L2Credentials(
        api_key=data["apiKey"],
        api_secret=data["secret"],
        api_passphrase=data["passphrase"],
    )
