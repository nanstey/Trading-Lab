from __future__ import annotations

from pathlib import Path

from trading_lab.data.hl_catalog import HyperliquidCatalog

from scripts.hl_backtest import _parse_args, _read_hypothesis, _resolve_coins


def test_parse_args_accepts_4h_interval() -> None:
    args = _parse_args([
        "--coin",
        "BTC",
        "--bar-interval",
        "4h",
        "--start",
        "2026-05-01",
        "--end",
        "2026-06-01",
    ])
    assert args.bar_interval == "4h"


def test_parse_args_accepts_2h_interval() -> None:
    args = _parse_args([
        "--coin",
        "BTC",
        "--bar-interval",
        "2h",
        "--start",
        "2026-05-01",
        "--end",
        "2026-06-01",
    ])
    assert args.bar_interval == "2h"


def test_read_hypothesis_supports_folder_dossier_layout(tmp_path: Path) -> None:
    hypotheses_dir = tmp_path / "research" / "hypotheses"
    dossier = hypotheses_dir / "hl-supertrend-cloud" / "dossier.md"
    dossier.parent.mkdir(parents=True)
    dossier.write_text(
        "---\n"
        "slug: hl-supertrend-cloud\n"
        "strategy_module: trading_lab.strategies.hl_supertrend_cloud\n"
        "strategy_class: HLSuperTrendCloudStrategy\n"
        "bar_interval: 4h\n"
        "---\n"
    )

    frontmatter = _read_hypothesis("hl-supertrend-cloud", hypotheses_dir=hypotheses_dir)

    assert frontmatter["strategy_module"] == "trading_lab.strategies.hl_supertrend_cloud"
    assert frontmatter["bar_interval"] == "4h"


def test_resolve_coins_prefers_hypothesis_symbols(tmp_path: Path) -> None:
    args = _parse_args([
        "--hypothesis-slug",
        "hl-supertrend-cloud",
        "--start",
        "2026-05-01",
        "--end",
        "2026-06-01",
    ])
    catalog = HyperliquidCatalog(tmp_path)

    coins = _resolve_coins(
        args,
        catalog,
        {
            "market_criteria": {
                "symbols": ["btc", "ETH"],
            }
        },
    )

    assert coins == ["BTC", "ETH"]
