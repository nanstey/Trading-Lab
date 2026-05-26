"""
System-wide configuration for Nautilus-Predict.

Three sources, merged at load time:

  1. **`.env`** — SECRETS ONLY (gitignored): wallet private keys, derived
     L2 API credentials, `LIVE_TRADING_CONFIRMED` security gate.
  2. **`config/system.yaml`** — log level, watcher thresholds, heartbeat
     timeout, budget caps. Tunable.
  3. **`config/venues.yaml`** — endpoint URLs + on-chain contract addresses.
     Constants; only change for testnet / chain migration.
  4. **`config/portfolio.yaml`** — risk envelope + (future) per-strategy
     capital allocations.

Strategy params are NOT system config — they live in the hypothesis
frontmatter (defaults from `*Config(StrategyConfig)` class) plus the
optimised winner row in `research/experiments.db`. Paper-vs-live is NOT
a system mode either — it's a per-strategy lifecycle state.

Usage:
    from nautilus_predict.config import load_config
    cfg = load_config()
    print(cfg.venues.polymarket.http_url)
    print(cfg.risk.daily_loss_limit_usdc)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Secrets (.env)
# ---------------------------------------------------------------------------


class PolymarketSecrets(BaseSettings):
    """Polymarket credentials only — endpoints come from venues.yaml."""

    model_config = SettingsConfigDict(env_prefix="POLY_", extra="ignore")

    private_key: SecretStr = Field(default=SecretStr(""))
    api_key: str = Field(default="")
    api_secret: SecretStr = Field(default=SecretStr(""))
    api_passphrase: SecretStr = Field(default=SecretStr(""))

    @field_validator("private_key", mode="before")
    @classmethod
    def strip_0x_prefix(cls, v: str | SecretStr) -> str:
        raw = v.get_secret_value() if isinstance(v, SecretStr) else v
        if raw.startswith("0x") or raw.startswith("0X"):
            return raw[2:]
        return raw

    @property
    def has_l1_credentials(self) -> bool:
        return bool(self.private_key.get_secret_value())

    @property
    def has_l2_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret.get_secret_value())


class HyperliquidSecrets(BaseSettings):
    """Hyperliquid credentials only — endpoints come from venues.yaml."""

    model_config = SettingsConfigDict(env_prefix="HL_", extra="ignore")

    private_key: SecretStr = Field(default=SecretStr(""))
    # Wallet address derived from private key; not a secret but lives with
    # the credentials since it's user-specific. Leave blank to derive.
    account_address: str = Field(default="")

    @field_validator("private_key", mode="before")
    @classmethod
    def strip_0x_prefix(cls, v: str | SecretStr) -> str:
        raw = v.get_secret_value() if isinstance(v, SecretStr) else v
        if raw.startswith("0x") or raw.startswith("0X"):
            return raw[2:]
        return raw

    @property
    def has_credentials(self) -> bool:
        return bool(self.private_key.get_secret_value())


# ---------------------------------------------------------------------------
# YAML-backed sections (committed config/ files)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolymarketVenue:
    http_url: str
    ws_market_url: str
    ws_user_url: str
    ctf_address: str
    exchange_address: str


@dataclass(frozen=True)
class HyperliquidVenue:
    api_url: str
    ws_url: str


@dataclass(frozen=True)
class PolygonVenue:
    rpc_url: str


@dataclass(frozen=True)
class VenuesConfig:
    polymarket: PolymarketVenue
    hyperliquid: HyperliquidVenue
    polygon: PolygonVenue


@dataclass(frozen=True)
class WatcherConfig:
    initial_capital_usdc: float
    single_day_limit_pct: float
    rolling_dd_limit_pct: float
    rolling_window_days: int


@dataclass(frozen=True)
class BudgetConfig:
    llm_tokens_per_day: int
    backtests_per_day: int
    paper_starts_per_week: int
    live_starts_per_day: int


@dataclass(frozen=True)
class SystemConfig:
    log_level: str
    heartbeat_timeout_secs: int
    watcher: WatcherConfig
    budget: BudgetConfig


@dataclass(frozen=True)
class RiskConfig:
    max_position_usdc: float
    max_total_exposure_usdc: float
    daily_loss_limit_usdc: float


@dataclass(frozen=True)
class PortfolioConfig:
    risk: RiskConfig
    # Per-strategy capital ceilings, vended on start by the (TODO) allocator.
    allocations: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level merged config
# ---------------------------------------------------------------------------


@dataclass
class TradingConfig:
    """
    Merged top-level config. Constructed by `load_config()`.

    Mutability note: this dataclass is intentionally not frozen so a few
    legacy code paths (e.g. backtest.py with --min-profit-usdc CLI flag)
    can still tweak fields in-place during a single process invocation.
    Don't mutate from production code; tweaks should go through the
    proper sources (YAML files or hypothesis MD).
    """

    # Secrets (from .env)
    polymarket_secrets: PolymarketSecrets
    hyperliquid_secrets: HyperliquidSecrets

    # YAML sections
    system: SystemConfig
    venues: VenuesConfig
    portfolio: PortfolioConfig

    # ----------- Backward-compatible accessors -----------
    # These shadow the old TradingConfig field names so existing call
    # sites keep working during the transition. Prefer the new paths
    # (cfg.venues.polymarket.http_url, cfg.portfolio.risk.daily_loss_limit_usdc).

    @property
    def polymarket(self) -> _PolymarketCompat:
        return _PolymarketCompat(self.polymarket_secrets, self.venues.polymarket)

    @property
    def hyperliquid(self) -> _HyperliquidCompat:
        return _HyperliquidCompat(self.hyperliquid_secrets, self.venues.hyperliquid)

    @property
    def risk(self) -> _RiskCompat:
        return _RiskCompat(self.portfolio.risk, self.system.heartbeat_timeout_secs)

    @property
    def log_level(self) -> str:
        return self.system.log_level

    # `is_live` / `is_paper` are now PER-STRATEGY concerns (hypothesis state),
    # NOT system state. Removed from the new config.


# ---------------------------------------------------------------------------
# Backward-compatibility shims — only used by legacy code during transition.
# ---------------------------------------------------------------------------


class _PolymarketCompat:
    """Lets `cfg.polymarket.host` etc. keep working for older callers."""

    def __init__(self, secrets: PolymarketSecrets, venue: PolymarketVenue) -> None:
        self._secrets = secrets
        self._venue = venue

    @property
    def private_key(self) -> SecretStr:
        return self._secrets.private_key

    @property
    def api_key(self) -> str:
        return self._secrets.api_key

    @property
    def api_secret(self) -> SecretStr:
        return self._secrets.api_secret

    @property
    def api_passphrase(self) -> SecretStr:
        return self._secrets.api_passphrase

    @property
    def has_l1_credentials(self) -> bool:
        return self._secrets.has_l1_credentials

    @property
    def has_l2_credentials(self) -> bool:
        return self._secrets.has_l2_credentials

    @property
    def host(self) -> str:
        return self._venue.http_url

    @property
    def ws_host(self) -> str:
        # Legacy: callers expected one ws URL. Default to market channel.
        return self._venue.ws_market_url

    @property
    def ws_market_url(self) -> str:
        return self._venue.ws_market_url

    @property
    def ws_user_url(self) -> str:
        return self._venue.ws_user_url

    @property
    def exchange_address(self) -> str:
        return self._venue.exchange_address


class _HyperliquidCompat:
    def __init__(self, secrets: HyperliquidSecrets, venue: HyperliquidVenue) -> None:
        self._secrets = secrets
        self._venue = venue

    @property
    def private_key(self) -> SecretStr:
        return self._secrets.private_key

    @property
    def account_address(self) -> str:
        return self._secrets.account_address

    @property
    def api_url(self) -> str:
        return self._venue.api_url

    @property
    def ws_url(self) -> str:
        return self._venue.ws_url

    @property
    def has_credentials(self) -> bool:
        return self._secrets.has_credentials


class _RiskCompat:
    def __init__(self, risk: RiskConfig, heartbeat_timeout_secs: int) -> None:
        self._risk = risk
        self._heartbeat = heartbeat_timeout_secs

    @property
    def max_position_usdc(self) -> float:
        return self._risk.max_position_usdc

    @property
    def max_total_exposure_usdc(self) -> float:
        return self._risk.max_total_exposure_usdc

    @property
    def daily_loss_limit_usdc(self) -> float:
        return self._risk.daily_loss_limit_usdc

    @property
    def heartbeat_timeout_secs(self) -> int:
        return self._heartbeat


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


CONFIG_DIR = Path("config")


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file: {path}. See config/*.example files (if "
            f"present) or run `cp config.example/* config/` to seed defaults."
        )
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_config() -> TradingConfig:
    """Load + validate full system configuration."""
    from dotenv import load_dotenv

    load_dotenv()

    polymarket_secrets = PolymarketSecrets()
    hyperliquid_secrets = HyperliquidSecrets()

    sys_yaml = _load_yaml("system.yaml")
    venues_yaml = _load_yaml("venues.yaml")
    portfolio_yaml = _load_yaml("portfolio.yaml")

    system = SystemConfig(
        log_level=sys_yaml.get("log_level", "INFO"),
        heartbeat_timeout_secs=int(sys_yaml.get("heartbeat_timeout_secs", 10)),
        watcher=WatcherConfig(**sys_yaml.get("watcher", {})),
        budget=BudgetConfig(**sys_yaml.get("budget", {})),
    )

    venues = VenuesConfig(
        polymarket=PolymarketVenue(**venues_yaml.get("polymarket", {})),
        hyperliquid=HyperliquidVenue(**venues_yaml.get("hyperliquid", {})),
        polygon=PolygonVenue(**venues_yaml.get("polygon", {})),
    )

    portfolio = PortfolioConfig(
        risk=RiskConfig(**portfolio_yaml.get("risk", {})),
        allocations=portfolio_yaml.get("allocations", {}) or {},
    )

    return TradingConfig(
        polymarket_secrets=polymarket_secrets,
        hyperliquid_secrets=hyperliquid_secrets,
        system=system,
        venues=venues,
        portfolio=portfolio,
    )


def live_trading_confirmed() -> bool:
    """Read the per-process LIVE_TRADING_CONFIRMED gate from env.

    Kept as an env var (not yaml) so it's harder to commit by accident
    and so the operator can flip it without editing a file.
    """
    return os.environ.get("LIVE_TRADING_CONFIRMED", "").lower() == "true"
