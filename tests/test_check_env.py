from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_env = _load_script_module("check_env_script", "scripts/check_env.py")


class _Secret:
    def __init__(self, value: str):
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _cfg(*, has_l1: bool = True, has_l2: bool = True, funder: str = ""):
    return SimpleNamespace(
        polymarket=SimpleNamespace(
            has_l1_credentials=has_l1,
            has_l2_credentials=has_l2,
            private_key=_Secret("priv"),
            api_key="api-key",
            api_secret=_Secret("api-secret"),
            api_passphrase=_Secret("api-pass"),
            funder=funder,
        ),
        venues=SimpleNamespace(
            polymarket=SimpleNamespace(http_url="https://clob.polymarket.com")
        ),
    )


@pytest.mark.asyncio
async def test_polymarket_auth_connectivity_skips_without_full_creds() -> None:
    result = await check_env.check_polymarket_auth_connectivity(_cfg(has_l2=False))
    assert result.passed is True
    assert result.value == "skipped"


@pytest.mark.asyncio
async def test_polymarket_auth_connectivity_accepts_funder_fallback(monkeypatch) -> None:
    async def fake_targets(cfg):
        return [("signer", "0xsigner"), ("configured-funder", "0xfunder")]

    monkeypatch.setattr(check_env, "_candidate_polymarket_auth_targets", fake_targets)

    calls: list[str] = []

    async def fake_probe(http_url: str, **kwargs) -> None:
        calls.append(kwargs["address"])
        if kwargs["address"] == "0xsigner":
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(check_env, "_probe_polymarket_clob_auth", fake_probe)

    result = await check_env.check_polymarket_auth_connectivity(_cfg(funder="0xfunder"))
    assert result.passed is True
    assert result.value == "HTTP 200"
    assert "configured-funder" in result.note
    assert calls == ["0xsigner", "0xfunder"]


@pytest.mark.asyncio
async def test_polymarket_auth_connectivity_reports_stale_creds(monkeypatch) -> None:
    async def fake_targets(cfg):
        return [("signer", "0xsigner")]

    monkeypatch.setattr(check_env, "_candidate_polymarket_auth_targets", fake_targets)

    async def fake_probe(http_url: str, **kwargs) -> None:
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(check_env, "_probe_polymarket_clob_auth", fake_probe)

    result = await check_env.check_polymarket_auth_connectivity(_cfg())
    assert result.passed is False
    assert result.value == "unauthorized"
    assert "stale POLY_API_* credentials" in result.note


@pytest.mark.asyncio
async def test_candidate_polymarket_auth_targets_adds_discovered_proxy(monkeypatch) -> None:
    async def fake_discover(addr: str) -> str | None:
        assert addr == "0xsigner"
        return "0xproxy"

    monkeypatch.setattr(check_env, "_discover_polymarket_proxy_wallet", fake_discover)

    from trading_lab.venues.polymarket import auth as pm_auth

    monkeypatch.setattr(pm_auth, "derive_address", lambda _: "0xsigner")

    targets = await check_env._candidate_polymarket_auth_targets(_cfg(funder=""))
    assert targets == [("signer", "0xsigner"), ("discovered-proxy", "0xproxy")]
