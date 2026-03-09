#!/usr/bin/env python3
"""
Environment and connectivity checker for Nautilus-Predict.

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
from typing import Optional

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


async def check_polymarket_connectivity(host: str) -> CheckResult:
    """Check if Polymarket CLOB API is reachable."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{host}/markets", params={"next_cursor": "MA=="})
            resp.raise_for_status()
            data = resp.json()
            market_count = len(data.get("data", []))
            return CheckResult(
                name="Polymarket API",
                passed=True,
                value=f"HTTP {resp.status_code}",
                note=f"{market_count} markets returned",
            )
    except ImportError:
        return CheckResult(
            name="Polymarket API",
            passed=False,
            value="httpx not installed",
            note="Run: pip install httpx",
        )
    except Exception as exc:
        return CheckResult(
            name="Polymarket API",
            passed=False,
            value="unreachable",
            note=str(exc)[:80],
        )


async def check_hyperliquid_connectivity(api_url: str) -> CheckResult:
    """Check if Hyperliquid API is reachable."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{api_url}/info",
                json={"type": "meta"},
            )
            resp.raise_for_status()
            data = resp.json()
            coin_count = len(data.get("universe", []))
            return CheckResult(
                name="Hyperliquid API",
                passed=True,
                value=f"HTTP {resp.status_code}",
                note=f"{coin_count} perpetual markets available",
            )
    except ImportError:
        return CheckResult(
            name="Hyperliquid API",
            passed=False,
            value="httpx not installed",
            note="Run: pip install httpx",
        )
    except Exception as exc:
        return CheckResult(
            name="Hyperliquid API",
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

    print(f"\n{BOLD}Nautilus-Predict Environment Check{RESET}")
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
        ("httpx", None),
        ("websockets", None),
        ("pyarrow", None),
        ("pandas", None),
        ("eth_account", "eth_account"),
    ]:
        report.add(check_package_installed(pkg, import_name))

    # Trading mode
    report.add(
        CheckResult(
            name="TRADING_MODE",
            passed=True,
            value=os.environ.get("TRADING_MODE", "paper (default)"),
        )
    )

    # Polymarket credentials
    report.add(check_env_var("POLY_PRIVATE_KEY", required=False, sensitive=True))
    report.add(check_env_var("POLY_API_KEY", required=False, sensitive=True))
    report.add(check_env_var("POLY_API_SECRET", required=False, sensitive=True))
    report.add(check_env_var("POLY_API_PASSPHRASE", required=False, sensitive=True))

    poly_host = os.environ.get("POLY_HOST", "https://clob.polymarket.com")
    report.add(
        CheckResult(name="POLY_HOST", passed=True, value=poly_host)
    )

    # Hyperliquid credentials
    report.add(check_env_var("HL_PRIVATE_KEY", required=False, sensitive=True))
    hl_url = os.environ.get("HL_API_URL", "https://api.hyperliquid.xyz")
    report.add(CheckResult(name="HL_API_URL", passed=True, value=hl_url))

    # Risk config
    report.add(
        CheckResult(
            name="MAX_POSITION_USDC",
            passed=True,
            value=os.environ.get("MAX_POSITION_USDC", "100.0 (default)"),
        )
    )
    report.add(
        CheckResult(
            name="DAILY_LOSS_LIMIT_USDC",
            passed=True,
            value=os.environ.get("DAILY_LOSS_LIMIT_USDC", "-200.0 (default)"),
        )
    )

    # Data directory
    report.add(check_data_directory(Path("./data")))

    # Live trading safety check
    trading_mode = os.environ.get("TRADING_MODE", "paper")
    live_confirmed = os.environ.get("LIVE_TRADING_CONFIRMED", "")
    if trading_mode == "live":
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
        report.add(await check_polymarket_connectivity(poly_host))
        report.add(await check_hyperliquid_connectivity(hl_url))
    else:
        print("Skipping connectivity checks (--no-connectivity)")

    return report


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Check Nautilus-Predict environment configuration",
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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = asyncio.run(run_checks(args))
    print_report(report, verbose=args.verbose)
    sys.exit(0 if report.all_passed else 1)
