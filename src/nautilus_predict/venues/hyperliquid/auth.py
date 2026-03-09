"""
Hyperliquid authentication.

Hyperliquid uses EIP-712 typed-data signing for all order actions. The signer
is an Ethereum EOA (the "agent" wallet). A separate "main" account address can
be specified to route trades through a vault or sub-account.

Reference: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/signing
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data


_HL_DOMAIN = {
    "name": "Exchange",
    "version": "1",
    "chainId": 1337,     # Hyperliquid L1 chain ID
    "verifyingContract": "0x0000000000000000000000000000000000000000",
}


def sign_l1_action(
    private_key: str,
    action: dict[str, Any],
    vault_address: str | None,
    nonce: int,
) -> str:
    """
    Sign a Hyperliquid L1 action (order, cancel, etc.) with EIP-712.

    Parameters
    ----------
    private_key : str
        Ethereum private key of the agent/signer wallet.
    action : dict
        The action payload (e.g. {"type": "order", ...}).
    vault_address : str | None
        Main account address if trading via a vault/sub-account; None otherwise.
    nonce : int
        Unique nonce (millisecond timestamp recommended).

    Returns
    -------
    str
        Hex-encoded EIP-712 signature (0x-prefixed).
    """
    # Hyperliquid hashes action + nonce + vault_address as the message
    connection_id = _build_connection_id(action, nonce, vault_address)

    structured_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
        },
        "domain": _HL_DOMAIN,
        "primaryType": "Agent",
        "message": {
            "source": "a" if vault_address else "b",
            "connectionId": connection_id,
        },
    }

    signable = encode_typed_data(full_message=structured_data)
    signed = Account.sign_message(signable, private_key=private_key)
    return signed.signature.hex()


def _build_connection_id(
    action: dict[str, Any],
    nonce: int,
    vault_address: str | None,
) -> bytes:
    """Keccak256 hash of the packed action + nonce + vault_address."""
    from eth_abi import encode
    from eth_utils import keccak

    action_bytes = json.dumps(action, separators=(",", ":"), sort_keys=True).encode()
    nonce_bytes = nonce.to_bytes(8, "big")

    if vault_address:
        vault_bytes = bytes.fromhex(vault_address.removeprefix("0x"))
        flag = b"\x01"
    else:
        vault_bytes = b""
        flag = b"\x00"

    packed = action_bytes + nonce_bytes + flag + vault_bytes
    return keccak(primitive=packed)


def derive_address(private_key: str) -> str:
    """Return the checksummed Ethereum address for a private key."""
    return Account.from_key(private_key).address


def current_nonce() -> int:
    """Return the current time in milliseconds as a nonce."""
    return int(time.time() * 1_000)
