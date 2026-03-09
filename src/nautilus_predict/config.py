"""
System-wide configuration loaded from environment variables.

All settings are validated via Pydantic at startup. Secrets are read from
.env (never hardcoded). Strategy parameters are tunable without code changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class PolymarketConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYMARKET_", extra="ignore")

    private_key: SecretStr = Field(..., description="Ethereum private key for L1 signing")
    api_key: str = Field(default="", description="L2 API key")
    api_secret: SecretStr = Field(default=SecretStr(""), description="L2 API secret")
    api_passphrase: SecretStr = Field(default=SecretStr(""), description="L2 API passphrase")

    http_url: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/"

    # Polygon network
    ctf_address: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    exchange_address: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

    @field_validator("private_key", mode="before")
    @classmethod
    def validate_private_key(cls, v: str) -> str:
        if isinstance(v, str) and v and not v.startswith("0x"):
            return f"0x{v}"
        return v


class HyperliquidConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HYPERLIQUID_", extra="ignore")

    private_key: SecretStr = Field(..., description="Ethereum private key for HL signing")
    account_address: str = Field(default="", description="Main account / vault address")

    http_url: str = "https://api.hyperliquid.xyz"
    ws_url: str = "wss://api.hyperliquid.xyz/ws"

    @field_validator("private_key", mode="before")
    @classmethod
    def validate_private_key(cls, v: str) -> str:
        if isinstance(v, str) and v and not v.startswith("0x"):
            return f"0x{v}"
        return v


class MarketMakerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MM_", extra="ignore")

    spread_bps: int = Field(default=50, ge=1, description="Minimum spread in basis points")
    order_size_usdc: float = Field(default=10.0, gt=0, description="Per-side order size (USDC)")
    max_position_usdc: float = Field(default=500.0, gt=0, description="Max net position (USDC)")


class ArbConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARB_", extra="ignore")

    min_profit_usdc: float = Field(default=0.02, ge=0, description="Minimum profit per arb")
    max_capital_usdc: float = Field(default=1000.0, gt=0, description="Max capital per arb")


class HedgeConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HEDGE_", extra="ignore")

    ratio: float = Field(default=0.5, ge=0.0, le=1.0, description="Hedge ratio")
    instrument: str = Field(default="BTC-USD", description="Hyperliquid hedge instrument")


class SystemConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    trading_mode: TradingMode = TradingMode.PAPER
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Sub-configs are populated from the same env namespace
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    hyperliquid: HyperliquidConfig = Field(default_factory=HyperliquidConfig)
    market_maker: MarketMakerConfig = Field(default_factory=MarketMakerConfig)
    arb: ArbConfig = Field(default_factory=ArbConfig)
    hedge: HedgeConfig = Field(default_factory=HedgeConfig)

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE


def load_config() -> SystemConfig:
    """Load and validate full system configuration from environment."""
    from dotenv import load_dotenv

    load_dotenv()
    return SystemConfig()
