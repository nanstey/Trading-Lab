from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hl_optimize = _load_script_module("hl_optimize_test", "scripts/hl_optimize.py")


def test_config_methodology_penalizes_insufficient_breadth() -> None:
    good_methodology, good_state, good_category, good_score = hl_optimize._config_methodology(
        oos_mean_sharpe=0.8,
        oos_total_pnl=100.0,
        oos_total_trades=150,
        oos_min_trades=20,
        oos_worst_dd=-8.0,
        n_markets=5,
        n_markets_with_fills=3,
    )
    weak_methodology, weak_state, weak_category, weak_score = hl_optimize._config_methodology(
        oos_mean_sharpe=1.2,
        oos_total_pnl=110.0,
        oos_total_trades=150,
        oos_min_trades=20,
        oos_worst_dd=-8.0,
        n_markets=5,
        n_markets_with_fills=1,
    )

    assert good_score > weak_score
    assert good_state == "SHELVED"
    assert good_category == "marginal_is"
    assert weak_state == "SHELVED"
    assert weak_category == "insufficient_breadth"
    assert "insufficient_breadth" in weak_methodology["sample_quality"]["warnings"]


def test_config_methodology_rejects_negative_expectancy() -> None:
    methodology, state, category, score = hl_optimize._config_methodology(
        oos_mean_sharpe=0.9,
        oos_total_pnl=-5.0,
        oos_total_trades=50,
        oos_min_trades=10,
        oos_worst_dd=-6.0,
        n_markets=3,
        n_markets_with_fills=2,
    )
    assert state == "REJECTED"
    assert category == "unprofitable"
    assert methodology["sample_quality"]["gates"]["positive_expectancy"] is False
    assert score < 2_000_000


def test_parse_hypothesis_supports_folder_dossier_layout(tmp_path: Path) -> None:
    hypotheses_dir = tmp_path / "research" / "hypotheses"
    dossier = hypotheses_dir / "hl-lunaowl-pricechannel" / "dossier.md"
    dossier.parent.mkdir(parents=True)
    dossier.write_text(
        "---\n"
        "slug: hl-lunaowl-pricechannel\n"
        "strategy_module: trading_lab.strategies.hl_lunaowl_pricechannel\n"
        "strategy_class: HLLunaOwlPriceChannelStrategy\n"
        "market_criteria:\n"
        "  symbols: [BTC, ETH]\n"
        "---\n\n"
        "## Parameter space\n"
        "- channel_length: [14, 21, 28]\n"
    )

    frontmatter, body = hl_optimize.parse_hypothesis("hl-lunaowl-pricechannel", hypotheses_dir)

    assert frontmatter["strategy_module"] == "trading_lab.strategies.hl_lunaowl_pricechannel"
    assert frontmatter["market_criteria"]["symbols"] == ["BTC", "ETH"]
    assert hl_optimize.parse_param_space(body) == {"channel_length": [14, 21, 28]}


def test_resolve_coins_prefers_hypothesis_symbols() -> None:
    coins = hl_optimize.resolve_coins(
        {"market_criteria": {"symbols": ["btc", "ETH"]}},
        universe=[],
    )

    assert coins == ["BTC", "ETH"]
