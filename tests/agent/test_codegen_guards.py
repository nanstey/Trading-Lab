"""Unit tests for codegen guards."""

from __future__ import annotations

from trading_lab.agent.codegen_guards import (
    _import_allowed,
    check_source,
)


class TestImportAllowed:
    def test_whitelisted_root(self):
        assert _import_allowed("numpy")
        assert _import_allowed("pandas")
        assert _import_allowed("nautilus_trader")

    def test_whitelisted_dotted(self):
        assert _import_allowed("nautilus_trader.model")
        assert _import_allowed("trading_lab.strategies.foo")

    def test_strict_no_substring_match(self):
        # 're' is whitelisted but 'requests' must not match by prefix
        assert not _import_allowed("requests")
        assert not _import_allowed("redis")
        assert not _import_allowed("os")
        assert not _import_allowed("subprocess")
        assert not _import_allowed("urllib")
        assert not _import_allowed("socket")


class TestCheckSource:
    def test_disallowed_import_violation(self):
        report = check_source("import requests")
        assert not report.ok
        assert report.first_category() == "import_violation"

    def test_relative_import_rejected(self):
        report = check_source("from . import foo")
        assert not report.ok
        assert report.first_category() == "import_violation"

    def test_lookahead_name_flagged(self):
        bad = (
            "from nautilus_trader.trading.strategy import Strategy\n"
            "class S(Strategy):\n"
            "    def on_t(self): x = self._future_price\n"
        )
        report = check_source(bad)
        assert not report.ok
        assert any(v.category == "lookahead_suspected" for v in report.violations)

    def test_clean_source_passes(self):
        ok = (
            "from nautilus_trader.trading.strategy import Strategy\n"
            "import numpy as np\n"
            "class S(Strategy):\n"
            "    def on_trade_tick(self, t): pass\n"
        )
        report = check_source(ok)
        assert report.ok

    def test_syntax_error_categorised(self):
        report = check_source("def broken(:")
        assert not report.ok
        assert report.first_category() == "syntax_error"
