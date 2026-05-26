"""Unit tests for the YAML+secrets configuration loader."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import yaml

from trading_lab.config import (
    HyperliquidSecrets,
    PolymarketSecrets,
    live_trading_confirmed,
    load_config,
)


@pytest.fixture
def yaml_config_dir(tmp_path, monkeypatch):
    """Each test gets its own config/ directory with fixtures."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "system.yaml").write_text(yaml.safe_dump({
        "log_level": "INFO",
        "heartbeat_timeout_secs": 10,
        "watcher": {
            "initial_capital_usdc": 10000,
            "single_day_limit_pct": 5,
            "rolling_dd_limit_pct": 15,
            "rolling_window_days": 7,
        },
        "budget": {
            "llm_tokens_per_day": 100000,
            "backtests_per_day": 50,
            "paper_starts_per_week": 1,
            "live_starts_per_day": 0,
        },
    }))
    (cfg_dir / "venues.yaml").write_text(yaml.safe_dump({
        "polymarket": {
            "http_url": "https://clob.polymarket.com",
            "ws_market_url": "wss://test/market",
            "ws_user_url": "wss://test/user",
            "ctf_address": "0xCtf",
            "exchange_address": "0xExch",
        },
        "hyperliquid": {"api_url": "https://hl/api", "ws_url": "wss://hl/ws"},
        "polygon": {"rpc_url": "https://polygon-rpc.example"},
    }))
    (cfg_dir / "portfolio.yaml").write_text(yaml.safe_dump({
        "risk": {
            "max_position_usdc": 100.0,
            "max_total_exposure_usdc": 1000.0,
            "daily_loss_limit_usdc": -200.0,
        },
        "allocations": {},
    }))
    # Point the loader at this temp dir.
    monkeypatch.setattr("trading_lab.config.CONFIG_DIR", cfg_dir)
    return cfg_dir


class TestPolymarketSecrets:
    def test_private_key_strip_0x(self):
        with patch.dict(os.environ, {"POLY_PRIVATE_KEY": "0x" + "ab" * 32}):
            s = PolymarketSecrets()
            assert not s.private_key.get_secret_value().startswith("0x")

    def test_no_prefix_unchanged(self):
        raw = "ab" * 32
        with patch.dict(os.environ, {"POLY_PRIVATE_KEY": raw}, clear=False):
            s = PolymarketSecrets()
            assert s.private_key.get_secret_value() == raw

    def test_has_l1_l2(self):
        env = {
            "POLY_PRIVATE_KEY": "ab" * 32,
            "POLY_API_KEY": "key",
            "POLY_API_SECRET": "secret",
            "POLY_API_PASSPHRASE": "pass",
        }
        with patch.dict(os.environ, env, clear=False):
            s = PolymarketSecrets()
            assert s.has_l1_credentials
            assert s.has_l2_credentials


class TestHyperliquidSecrets:
    def test_defaults_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            s = HyperliquidSecrets()
            assert not s.has_credentials


class TestLoadConfig:
    def test_loads_yaml(self, yaml_config_dir):
        cfg = load_config()
        assert cfg.venues.polymarket.http_url == "https://clob.polymarket.com"
        assert cfg.portfolio.risk.daily_loss_limit_usdc == -200.0
        assert cfg.system.watcher.single_day_limit_pct == 5
        assert cfg.system.budget.backtests_per_day == 50

    def test_legacy_compat_accessors(self, yaml_config_dir):
        cfg = load_config()
        # Old call sites use cfg.polymarket.host / cfg.risk.daily_loss_limit_usdc
        assert cfg.polymarket.host == "https://clob.polymarket.com"
        assert cfg.risk.daily_loss_limit_usdc == -200.0
        assert cfg.risk.heartbeat_timeout_secs == 10
        # ws_host returns market url for legacy paths
        assert cfg.polymarket.ws_host == "wss://test/market"
        assert cfg.polymarket.ws_market_url == "wss://test/market"
        assert cfg.polymarket.ws_user_url == "wss://test/user"

    def test_log_level_property(self, yaml_config_dir):
        cfg = load_config()
        assert cfg.log_level == "INFO"


class TestLiveTradingConfirmed:
    def test_default_false(self):
        with patch.dict(os.environ, {}, clear=True):
            assert not live_trading_confirmed()

    def test_true_when_set(self):
        with patch.dict(os.environ, {"LIVE_TRADING_CONFIRMED": "true"}):
            assert live_trading_confirmed()

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"LIVE_TRADING_CONFIRMED": "TRUE"}):
            assert live_trading_confirmed()
