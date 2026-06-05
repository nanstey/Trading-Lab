#!/usr/bin/env python3
"""
Environment and connectivity checker for Trading Lab.

Validates that all required environment variables are set and checks
connectivity to the Polymarket and Hyperliquid APIs. Safe to run
without real credentials - shows what's missing.

Usage:
    python scripts/check_env.py
    python scripts/check_env.py --verbose
    python scripts/check_env.py --no-connectivity  # skip API calls
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, use raw env


@dataclass
class CheckResult:
    """Result of a single environment or connectivity check."""

    name: str
    passed: bool
    value: str = ""
    note: str = ""


@dataclass
class CheckReport:
    """Aggregated results of all checks."""

    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def all_passed(self) -> bool:
        return self.failed_count == 0


def check_env_var(
    name: str,
    required: bool = True,
    sensitive: bool = False,
    expected_prefix: str | None = None,
) -> CheckResult:
    """Check if an environment variable is set and optionally validate format."""
    value = os.environ.get(name, "")

    if not value:
        return CheckResult(
            name=name,
            passed=not required,
            value="(not set)",
            note="REQUIRED" if required else "optional",
        )

    # Validate format if expected_prefix is given
    if expected_prefix and not value.startswith(expected_prefix):
        return CheckResult(
            name=name,
            passed=False,
            value=f"(set, wrong format - expected prefix '{expected_prefix}')",
            note=f"value should start with '{expected_prefix}'",
        )

    # Mask sensitive values
    display_value = value[:4] + "..." + value[-4:] if sensitive and len(value) > 8 else value
    return CheckResult(name=name, passed=True, value=display_value)


async def check_polymarket_connectivity(_host: str) -> CheckResult:
    """Check if Polymarket data API is reachable (public, no auth required)."""
    data_api = "https://data-api.polymarket.com"
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
            async with session.get(f"{data_api}/trades", params={"limit": 1}) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return CheckResult(
                    name="Polymarket API",
                    passed=True,
                    value=f"HTTP {resp.status}",
                    note=f"data-api reachable, {len(data)} record(s) returned",
                )
    except Exception as exc:
        return CheckResult(
            name="Polymarket API",
            passed=False,
            value="unreachable",
            note=str(exc)[:80],
        )


def _candidate_polymarket_auth_addresses(cfg) -> list[str]:
    from trading_lab.venues.polymarket.auth import derive_address

    signer = derive_address(cfg.polymarket.private_key.get_secret_value())
    addresses = [signer]
    funder = (cfg.polymarket.funder or "").strip()
    if funder and funder.lower() != signer.lower():
        addresses.append(funder)
    return addresses


async def _probe_polymarket_clob_auth(
    http_url: str,
    *,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    address: str,
) -> None:
    from trading_lab.venues.polymarket.auth import L2Credentials
    from trading_lab.venues.polymarket.client import PolymarketRestClient

    rest = PolymarketRestClient(
        http_url=http_url,
        creds=L2Credentials(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            address=address,
        ),
    )
    try:
        await rest._get("/balance-allowance", params={"asset_type": "COLLATERAL"}, auth=True)
    finally:
        await rest.close()


async def check_polymarket_auth_connectivity(cfg) -> CheckResult:
    """Check whether configured Polymarket L2 creds can hit an authenticated CLOB endpoint."""
    if not cfg.polymarket.has_l1_credentials or not cfg.polymarket.has_l2_credentials:
        return CheckResult(
            name="Polymarket CLOB auth",
            passed=True,
            value="skipped",
            note="POLY_PRIVATE_KEY + POLY_API_* not fully configured",
        )

    try:
        addresses = _candidate_polymarket_auth_addresses(cfg)
    except Exception as exc:
        return CheckResult(
            name="Polymarket CLOB auth",
            passed=False,
            value="wallet-derive-failed",
            note=str(exc)[:120],
        )

    failures: list[str] = []
    for idx, addr in enumerate(addresses):
        try:
            await _probe_polymarket_clob_auth(
                cfg.venues.polymarket.http_url,
                api_key=cfg.polymarket.api_key,
                api_secret=cfg.polymarket.api_secret.get_secret_value(),
                api_passphrase=cfg.polymarket.api_passphrase.get_secret_value(),
                address=addr,
            )
            mode = "signer" if idx == 0 else "funder"
            return CheckResult(
                name="Polymarket CLOB auth",
                passed=True,
                value="HTTP 200",
                note=f"authenticated balance/allowance check passed via {mode} address",
            )
        except Exception as exc:
            failures.append(f"{addr}: {exc}")

    return CheckResult(
        name="Polymarket CLOB auth",
        passed=False,
        value="unauthorized",
        note=(
            "Authenticated CLOB check failed for all candidate addresses; "
            "stale POLY_API_* credentials or wrong POLY_FUNDER/POLY_SIGNATURE_TYPE. "
            f"Last errors: {' | '.join(failures)[:180]}"
        ),
    )


async def check_hyperliquid_connectivity(
    api_url: str, network: str = "mainnet"
) -> CheckResult:
    """Check if a Hyperliquid endpoint is reachable.

    `network` is mainnet or testnet; it's used only for labelling the
    result so the operator can tell which endpoint the ping reflects.
    """
    label = f"Hyperliquid API ({network})"
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
            async with session.post(f"{api_url}/info", json={"type": "meta"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
                coin_count = len(data.get("universe", []))
                return CheckResult(
                    name=label,
                    passed=True,
                    value=f"HTTP {resp.status}",
                    note=f"{coin_count} perpetual markets available",
                )
    except Exception as exc:
        return CheckResult(
            name=label,
            passed=False,
            value="unreachable",
            note=str(exc)[:80],
        )


def check_python_version() -> CheckResult:
    """Check Python version requirement."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"
    passed = version >= (3, 12)
    return CheckResult(
        name="Python version",
        passed=passed,
        value=version_str,
        note="" if passed else "Python 3.12+ required",
    )


def check_package_installed(package_name: str, import_name: str | None = None) -> CheckResult:
    """Check if a Python package is importable."""
    import_name = import_name or package_name
    try:
        import importlib
        mod = importlib.import_module(import_name)
        version = getattr(mod, "__version__", "unknown version")
        return CheckResult(name=f"Package: {package_name}", passed=True, value=version)
    except ImportError:
        return CheckResult(
            name=f"Package: {package_name}",
            passed=False,
            value="not installed",
            note=f"Run: pip install {package_name}",
        )


def check_data_directory(data_dir: Path) -> CheckResult:
    """Check if the data directory exists and is writable."""
    if not data_dir.exists():
        return CheckResult(
            name="Data directory",
            passed=False,
            value=str(data_dir),
            note="Directory does not exist. Run: mkdir -p data/parquet",
        )
    if not os.access(data_dir, os.W_OK):
        return CheckResult(
            name="Data directory",
            passed=False,
            value=str(data_dir),
            note="Directory is not writable",
        )
    return CheckResult(name="Data directory", passed=True, value=str(data_dir))


def print_report(report: CheckReport, verbose: bool = False) -> None:
    """Print a formatted checklist report."""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

    print(f"\n{BOLD}Trading Lab Environment Check{RESET}")
    print("=" * 60)

    for result in report.results:
        icon = f"{GREEN}[PASS]{RESET}" if result.passed else f"{RED}[FAIL]{RESET}"
        line = f"{icon} {result.name}"
        if verbose or not result.passed:
            if result.value and result.value != "(not set)":
                line += f" = {result.value}"
            if result.note:
                line += f"\n       {YELLOW}Note: {result.note}{RESET}"
        print(line)

    print("=" * 60)
    status_color = GREEN if report.all_passed else RED
    print(
        f"\n{status_color}{BOLD}"
        f"{report.passed_count}/{report.passed_count + report.failed_count} checks passed"
        f"{RESET}"
    )

    if report.failed_count > 0:
        print(f"\n{RED}Action required:{RESET}")
        for result in report.results:
            if not result.passed and result.note:
                print(f"  - {result.name}: {result.note}")


async def run_checks(args: argparse.Namespace) -> CheckReport:
    """Run all environment and connectivity checks."""
    report = CheckReport()

    print("Running environment checks...")

    # Python version
    report.add(check_python_version())

    # Core packages
    for pkg, import_name in [
        ("nautilus_trader", None),
        ("pydantic", None),
        ("pydantic_settings", "pydantic_settings"),
        ("aiohttp", None),
        ("websockets", None),
        ("pyarrow", None),
        ("pandas", None),
        ("eth_account", "eth_account"),
    ]:
        report.add(check_package_installed(pkg, import_name))

    # Config files (YAML — committed to git)
    for yaml_name in ("system.yaml", "portfolio.yaml"):
        path = Path("config") / yaml_name
        report.add(CheckResult(
            name=f"config/{yaml_name}",
            passed=path.exists(),
            value="present" if path.exists() else "MISSING",
        ))

    # Polymarket credentials (secrets only — endpoint URLs are code constants)
    report.add(check_env_var("POLY_PRIVATE_KEY", required=False, sensitive=True))
    report.add(check_env_var("POLY_API_KEY", required=False, sensitive=True))
    report.add(check_env_var("POLY_API_SECRET", required=False, sensitive=True))
    report.add(check_env_var("POLY_API_PASSPHRASE", required=False, sensitive=True))
    report.add(check_env_var("POLY_FUNDER", required=False, sensitive=False))
    report.add(check_env_var("POLY_SIGNATURE_TYPE", required=False, sensitive=False))

    # Surface the venue endpoint we'll actually use (defined in venues.<v>.endpoints).
    _cfg = None
    try:
        from trading_lab.config import load_config
        _cfg = load_config()
        report.add(CheckResult(
            name="venues.polymarket.http_url",
            passed=True, value=_cfg.venues.polymarket.http_url,
        ))
        report.add(CheckResult(
            name="venues.hyperliquid.mainnet.api_url",
            passed=True, value=_cfg.venues.hyperliquid.mainnet.api_url,
        ))
        report.add(CheckResult(
            name="venues.hyperliquid.testnet.api_url",
            passed=True, value=_cfg.venues.hyperliquid.testnet.api_url,
        ))
        report.add(CheckResult(
            name="venues.hyperliquid.default_network",
            passed=True, value=_cfg.venues.hyperliquid.default_network,
        ))
        report.add(CheckResult(
            name="portfolio.risk.daily_loss_limit_usdc",
            passed=_cfg.portfolio.risk.daily_loss_limit_usdc < 0,
            value=str(_cfg.portfolio.risk.daily_loss_limit_usdc),
        ))
    except Exception as exc:
        report.add(CheckResult(name="config load", passed=False, value=f"FAILED: {exc}"))

    # Hyperliquid credentials — separate slots per network.
    report.add(check_env_var("HL_PRIVATE_KEY", required=False, sensitive=True))
    report.add(check_env_var("HL_TESTNET_PRIVATE_KEY", required=False, sensitive=True))

    # Data directory
    report.add(check_data_directory(Path("./data")))

    # Live trading safety check — gate is now `LIVE_TRADING_CONFIRMED`
    # alone (paper-vs-live is per-strategy via hypothesis state).
    live_confirmed = os.environ.get("LIVE_TRADING_CONFIRMED", "")
    if live_confirmed.lower() == "true":
        report.add(
            CheckResult(
                name="LIVE_TRADING_CONFIRMED",
                passed=live_confirmed.lower() == "true",
                value=live_confirmed or "(not set)",
                note="Must be 'true' to enable live trading" if live_confirmed.lower() != "true" else "",
            )
        )
    else:
        report.add(
            CheckResult(
                name="LIVE_TRADING_CONFIRMED",
                passed=True,
                value="(not in live mode - OK)",
            )
        )

    # Connectivity checks
    if not args.no_connectivity:
        print("Checking API connectivity...")
        # Read endpoints from load_config (sourced from venues.<v>.endpoints).
        from trading_lab.venues.polymarket.endpoints import HTTP_URL as PM_HTTP_URL
        poly_host = _cfg.venues.polymarket.http_url if _cfg is not None else PM_HTTP_URL
        report.add(await check_polymarket_connectivity(poly_host))
        if _cfg is not None:
            report.add(await check_polymarket_auth_connectivity(_cfg))

        # Hyperliquid: ping the requested network. If both wallet keys are
        # present (or `--network all`), ping both networks so the operator
        # gets a full picture.
        if _cfg is not None:
            hl = _cfg.venues.hyperliquid
            poly_secrets = _cfg.hyperliquid_secrets
        else:
            hl = None
            poly_secrets = None

        if hl is None:
            from trading_lab.venues.hyperliquid.endpoints import MAINNET_HTTP_URL
            report.add(await check_hyperliquid_connectivity(
                MAINNET_HTTP_URL, network="mainnet",
            ))
        else:
            networks: list[str] = []
            if args.network == "all":
                networks = ["mainnet", "testnet"]
            elif args.network in ("mainnet", "testnet"):
                networks = [args.network]
            else:
                # Default: ping whichever network has creds; always
                # include the configured default_network.
                networks = [hl.default_network]
                if poly_secrets is not None:
                    if poly_secrets.has_testnet_credentials and "testnet" not in networks:
                        networks.append("testnet")
                    if poly_secrets.has_credentials and "mainnet" not in networks:
                        networks.append("mainnet")
            for net in networks:
                report.add(await check_hyperliquid_connectivity(
                    hl.active(net).api_url, network=net,
                ))
    else:
        print("Skipping connectivity checks (--no-connectivity)")

    return report


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Check Trading Lab environment configuration",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all check values (not just failures)",
    )
    parser.add_argument(
        "--no-connectivity",
        action="store_true",
        help="Skip API connectivity checks",
    )
    parser.add_argument(
        "--network",
        choices=("mainnet", "testnet", "all", "auto"),
        default="auto",
        help="Which Hyperliquid network(s) to ping. 'auto' (default) pings "
             "the configured default + any network with credentials present.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = asyncio.run(run_checks(args))
    print_report(report, verbose=args.verbose)
    sys.exit(0 if report.all_passed else 1)
