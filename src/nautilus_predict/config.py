"""
System-wide configuration for Nautilus-Predict.

All settings are validated via Pydantic at startup. Secrets are read from
environment variables (via .env file). Safe defaults prevent live trading
without explicit opt-in.

Usage:
    from nautilus_predict.config import TradingConfig, TradingMode
    config = TradingConfig()
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    """Execution mode for the trading system."""

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class PolymarketConfig(BaseSettings):
    """Polymarket CLOB adapter configuration."""

    model_config = SettingsConfigDict(env_prefix="POLY_", extra="ignore")

    # L1 credentials
    private_key: SecretStr = Field(
        default=SecretStr(""),
        description="Ethereum/Polygon private key for L1 EIP-712 signing (hex, no 0x prefix)",
    )

    # L2 credentials (derived from L1 or pre-generated)
    api_key: str = Field(default="", description="Polymarket L2 API key")
    api_secret: SecretStr = Field(default=SecretStr(""), description="Polymarket L2 API secret")
    api_passphrase: SecretStr = Field(
        default=SecretStr(""), description="Polymarket L2 API passphrase"
    )

    # Endpoints
    host: str = Field(default="https://clob.polymarket.com", description="Polymarket CLOB REST URL")
    ws_host: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws/",
        description="Polymarket WebSocket URL",
    )

    # Contract address (Polygon mainnet constant — safe to default)
    exchange_address: str = Field(
        default="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        description="Polymarket CLOB Exchange contract address on Polygon",
    )

    @field_validator("private_key", mode="before")
    @classmethod
    def strip_0x_prefix(cls, v: str | SecretStr) -> str:
        """Normalize private key by stripping 0x prefix if present."""
        raw = v.get_secret_value() if isinstance(v, SecretStr) else v
        if raw.startswith("0x") or raw.startswith("0X"):
            return raw[2:]
        return raw

    @property
    def has_l1_credentials(self) -> bool:
        """Return True if L1 private key is configured."""
        return bool(self.private_key.get_secret_value())

    @property
    def has_l2_credentials(self) -> bool:
        """Return True if L2 API credentials are configured."""
        return bool(self.api_key and self.api_secret.get_secret_value())


class HyperliquidConfig(BaseSettings):
    """Hyperliquid adapter configuration."""

    model_config = SettingsConfigDict(env_prefix="HL_", extra="ignore")

    private_key: SecretStr = Field(
        default=SecretStr(""),
        description="Hyperliquid wallet private key for signing orders",
    )
    api_url: str = Field(
        default="https://api.hyperliquid.xyz", description="Hyperliquid REST API URL"
    )
    ws_url: str = Field(
        default="wss://api.hyperliquid.xyz/ws", description="Hyperliquid WebSocket URL"
    )
    account_address: str = Field(
        default="",
        description="EVM wallet address (leave blank to derive from private_key)",
    )

    @field_validator("private_key", mode="before")
    @classmethod
    def strip_0x_prefix(cls, v: str | SecretStr) -> str:
        """Normalize private key by stripping 0x prefix if present."""
        raw = v.get_secret_value() if isinstance(v, SecretStr) else v
        if raw.startswith("0x") or raw.startswith("0X"):
            return raw[2:]
        return raw

    @property
    def has_credentials(self) -> bool:
        """Return True if Hyperliquid credentials are configured."""
        return bool(self.private_key.get_secret_value())


class RiskConfig(BaseSettings):
    """Risk management configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    max_position_usdc: float = Field(
        default=100.0,
        gt=0,
        description="Maximum position size in USDC per market",
        alias="MAX_POSITION_USDC",
    )
    max_total_exposure_usdc: float = Field(
        default=1000.0,
        gt=0,
        description="Maximum total portfolio exposure in USDC",
        alias="MAX_TOTAL_EXPOSURE_USDC",
    )
    daily_loss_limit_usdc: float = Field(
        default=-200.0,
        lt=0,
        description="Kill switch triggers if daily PnL drops below this threshold (must be negative)",
        alias="DAILY_LOSS_LIMIT_USDC",
    )
    heartbeat_timeout_secs: int = Field(
        default=10,
        gt=0,
        description="Heartbeat timeout in seconds before canceling all orders",
        alias="HEARTBEAT_TIMEOUT_SECS",
    )

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class MarketMakerConfig(BaseSettings):
    """Market maker strategy parameters."""

    model_config = SettingsConfigDict(env_prefix="MM_", extra="ignore")

    spread_bps: float = Field(default=50.0, ge=1.0, description="Bid-ask spread to quote in basis points")
    order_size_usdc: float = Field(default=10.0, ge=1.0, description="Per-side order size in USDC")
    max_position_usdc: float = Field(default=500.0, ge=1.0, description="Max net position per market in USDC")


class ArbConfig(BaseSettings):
    """Binary complement arbitrage strategy parameters."""

    model_config = SettingsConfigDict(env_prefix="ARB_", extra="ignore")

    min_profit_usdc: float = Field(default=0.02, ge=0.0, description="Minimum profit per arb after fees (USDC)")
    max_capital_usdc: float = Field(default=1000.0, ge=1.0, description="Maximum capital per arb opportunity (USDC)")


class HedgeConfig(BaseSettings):
    """Cross-venue hedge strategy parameters (Polymarket → Hyperliquid)."""

    model_config = SettingsConfigDict(env_prefix="HEDGE_", extra="ignore")

    ratio: float = Field(default=0.5, ge=0.0, le=1.0, description="Fraction of Polymarket exposure to hedge (0.0–1.0)")
    instrument: str = Field(default="BTC-USD", description="Hyperliquid instrument for hedging")


class TradingConfig(BaseSettings):
    """
    Top-level configuration for Nautilus-Predict.

    Reads from environment variables and .env file. Safe defaults
    prevent live trading without explicit opt-in.

    Safe default: TRADING_MODE=paper (never live without explicit config).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
        description="Execution mode: backtest | paper | live",
        alias="TRADING_MODE",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging verbosity",
        alias="LOG_LEVEL",
    )

    # Nested configs loaded from their own env prefixes
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    hyperliquid: HyperliquidConfig = Field(default_factory=HyperliquidConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    market_maker: MarketMakerConfig = Field(default_factory=MarketMakerConfig)
    arb: ArbConfig = Field(default_factory=ArbConfig)
    hedge: HedgeConfig = Field(default_factory=HedgeConfig)

    @model_validator(mode="after")
    def validate_live_mode_requirements(self) -> TradingConfig:
        """
        Enforce double opt-in for live trading.

        Live mode requires BOTH:
          - TRADING_MODE=live
          - LIVE_TRADING_CONFIRMED=true

        This prevents accidentally starting live trading.
        """
        if self.trading_mode == TradingMode.LIVE:
            confirmed = os.environ.get("LIVE_TRADING_CONFIRMED", "").lower()
            if confirmed != "true":
                raise ValueError(
                    "Live trading requires LIVE_TRADING_CONFIRMED=true in environment. "
                    "Set this explicitly to confirm you intend to trade with real funds."
                )
        return self

    @property
    def is_live(self) -> bool:
        """Return True if running in live trading mode."""
        return self.trading_mode == TradingMode.LIVE

    @property
    def is_paper(self) -> bool:
        """Return True if running in paper trading mode."""
        return self.trading_mode == TradingMode.PAPER

    @property
    def is_backtest(self) -> bool:
        """Return True if running in backtest mode."""
        return self.trading_mode == TradingMode.BACKTEST


SystemConfig = TradingConfig


def load_config() -> TradingConfig:
    """Load and validate full system configuration from environment."""
    from dotenv import load_dotenv

    load_dotenv()
    return TradingConfig()
