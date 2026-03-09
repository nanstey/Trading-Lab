# Polymarket Authentication Guide

## Overview

Polymarket uses a two-level authentication system:

- **Level 1 (L1)**: On-chain identity via Ethereum private key and EIP-712 signing
- **Level 2 (L2)**: Off-chain API credentials derived from L1, used for HMAC-SHA256 request signing

You interact with L1 once to derive or register L2 credentials. All subsequent
API calls use L2 credentials with HMAC-SHA256 signatures.

---

## Level 1: EIP-712 Wallet Authentication

### What it is

L1 authentication proves ownership of an Ethereum wallet (on Polygon/Polygon mainnet).
This wallet holds your USDC collateral and is the source of truth for your account identity.

### How it works

```
Private Key (hex)
    │
    │  eth_account.Account.from_key()
    ▼
Ethereum Account (address + signing capability)
    │
    │  Sign EIP-712 typed data:
    │  domain: { name: "ClobAuthDomain", chainId: 137 }
    │  type: ClobAuth { address, timestamp, nonce, message }
    ▼
EIP-712 Signature (0x-prefixed hex)
```

### The EIP-712 message structure

```json
{
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"}
        ],
        "ClobAuth": [
            {"name": "address", "type": "address"},
            {"name": "timestamp", "type": "string"},
            {"name": "nonce", "type": "int256"},
            {"name": "message", "type": "string"}
        ]
    },
    "domain": {
        "name": "ClobAuthDomain",
        "version": "1",
        "chainId": 137
    },
    "primaryType": "ClobAuth",
    "message": {
        "address": "0xYourAddress",
        "timestamp": "1700000000",
        "nonce": 0,
        "message": "This message attests that I control the given wallet"
    }
}
```

### L1 Auth Headers

When using L1 signing (e.g., for credential derivation):

```
POLY-ADDRESS: 0xYourAddress
POLY-SIGNATURE: 0x<eip712-signature>
POLY-TIMESTAMP: <unix-timestamp>
POLY-NONCE: 0
```

---

## Level 2: API Credential Derivation

### Deriving L2 credentials

Call `GET /auth/derive-api-key` with L1 headers to receive L2 credentials:

```python
from nautilus_predict.adapters.polymarket.auth import PolymarketAuth

auth = PolymarketAuth(private_key="your_private_key")
creds = await auth.derive_l2_credentials(
    host="https://clob.polymarket.com",
    nonce=0,  # Increment to rotate credentials
)
print(creds.api_key)        # UUID
print(creds.api_secret)     # base64-encoded HMAC secret
print(creds.api_passphrase) # passphrase
```

**Important**: Store these credentials in your `.env` file. Avoid re-deriving on
every startup as this creates unnecessary on-chain signatures.

```bash
POLY_API_KEY=<uuid from derivation>
POLY_API_SECRET=<base64-encoded secret>
POLY_API_PASSPHRASE=<passphrase>
```

### Credential rotation

To rotate credentials, increment the nonce and re-derive:

```python
new_creds = await auth.derive_l2_credentials(nonce=1)  # Old creds (nonce=0) are invalidated
```

---

## Level 2: HMAC-SHA256 Request Signing

### Signature construction

Every authenticated API request must include HMAC-SHA256 signed headers.

The signature message is constructed as:
```
message = timestamp + METHOD + path + body
```

Where:
- `timestamp` = Unix timestamp as string (seconds since epoch)
- `METHOD` = HTTP method in uppercase ("GET", "POST", "DELETE")
- `path` = Request path including query string
- `body` = JSON-encoded request body (empty string for GET)

The HMAC key is the **base64-decoded** api_secret.

### Python implementation

```python
import hashlib
import hmac
import time
from base64 import b64decode, b64encode

def sign_request(api_secret: str, method: str, path: str, body: str = "") -> str:
    timestamp = str(int(time.time()))
    message = timestamp + method.upper() + path + body

    secret_bytes = b64decode(api_secret)
    sig = hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256).digest()
    return b64encode(sig).decode("utf-8")
```

### L2 Request Headers

```
POLY-ADDRESS: <api_key>           # The api_key acts as the address identifier
POLY-SIGNATURE: <hmac_signature>  # base64-encoded HMAC-SHA256
POLY-TIMESTAMP: <unix_timestamp>
POLY-API-KEY: <api_key>
POLY-PASSPHRASE: <api_passphrase>
```

---

## Fee-Aware Quoting

**Critical**: All Polymarket orders must include a `feeRateBps` field.

Omitting this field will cause order rejection with a fee validation error.

### Fetching the fee rate

```python
fee_bps = await client.get_fee_rate_bps(token_id="0xabc...")
```

For most standard markets with the maker rebate model:
- Maker orders: `feeRateBps = 0` (no fee, may receive rebate)
- Taker orders: `feeRateBps = 20` (20 bps = 0.20%)

### Including in order payload

```json
{
    "token_id": "0xabc...",
    "side": "BUY",
    "price": "0.65",
    "size": "10.0",
    "type": "LIMIT",
    "feeRateBps": 0
}
```

---

## Heartbeat Endpoint

Polymarket WebSocket connections require periodic heartbeats.
The heartbeat keeps the session alive and confirms connectivity.

### REST heartbeat

```python
# Returns True if alive, False if failed
is_alive = await client.heartbeat()
```

The `HeartbeatWatcher` monitors this automatically:
- Polls every `timeout_secs / 2` seconds
- Triggers kill switch + cancel all orders after 2 consecutive failures

### WebSocket heartbeat

For WebSocket connections, Polymarket sends Ping frames that must be
responded to with Pong frames. `polyfill-rs` handles this automatically
in the message loop.

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `InvalidSignature` | Wrong HMAC key format | Ensure api_secret is base64-decoded before use |
| `ExpiredTimestamp` | Clock drift | Sync system clock (NTP) |
| `MissingFeeRateBps` | Order missing fee field | Include `feeRateBps` in all orders |
| `InvalidNonce` | Nonce already used | Increment nonce for credential rotation |
| `Unauthorized` | Wrong api_key format | api_key should be UUID, not address |

---

## Reference

- Official Polymarket API docs: https://docs.polymarket.com/#authentication
- py-clob-client (reference implementation): https://github.com/Polymarket/py-clob-client
- EIP-712 spec: https://eips.ethereum.org/EIPS/eip-712

---

## TODO

- [ ] Confirm exact `feeRateBps` endpoint path from official docs
- [ ] Document neg-risk market fee rates (differ from standard markets)
- [ ] Add example of full order signing flow with test vectors
- [ ] Document WebSocket authentication for user channel
