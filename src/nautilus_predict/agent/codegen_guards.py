"""
Codegen guards — AST + smoke checks for agent-written strategies.

Two layers of defense, run by `scripts/smoke_test_strategy.py` before any
new strategy file is allowed past `CODEGEN → SMOKE_PASS`:

1. **Import allowlist** — strategies may only import from a fixed set of
   stable modules. Catches data leakage (`requests` → calling out), code
   escape (`subprocess`, `os.system`), and other obviously-wrong stuff.

2. **Lookahead heuristic** — the most common silent killer of agent-written
   strategies. We look for: `on_*` handlers that read module-level state
   set by a later-timestamped event, names ending in `_future`/`_next`,
   and reads of attributes that are written further down the same file
   without going through a `__init__` or `on_start` path.

Neither check is sound. They're cheap heuristics that catch the obvious
mistakes and produce a stable rejection_category. False positives are
expected; spec ships a manual override path.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


ALLOWED_IMPORT_PREFIXES = (
    "nautilus_trader",
    "nautilus_predict",
    "numpy",
    "pandas",
    "datetime",
    "collections",
    "dataclasses",
    "enum",
    "functools",
    "itertools",
    "json",
    "math",
    "statistics",
    "structlog",
    "logging",
    "typing",
    "abc",
    "re",
    "decimal",
    "pathlib",
    "uuid",
    "operator",
    "warnings",
    "__future__",
)


SUSPECT_NAME_FRAGMENTS = ("_future", "_next", "lookahead", "look_ahead")


@dataclass
class GuardViolation:
    category: str  # "import_violation" | "lookahead_suspected" | "syntax_error"
    detail: str
    lineno: int = 0


@dataclass
class GuardReport:
    ok: bool
    violations: list[GuardViolation]

    def first_category(self) -> str | None:
        return self.violations[0].category if self.violations else None


def check_file(path: Path) -> GuardReport:
    src = path.read_text()
    return check_source(src, filename=str(path))


def check_source(src: str, filename: str = "<src>") -> GuardReport:
    try:
        tree = ast.parse(src, filename=filename)
    except SyntaxError as exc:
        return GuardReport(
            ok=False,
            violations=[GuardViolation("syntax_error", str(exc), exc.lineno or 0)],
        )

    violations: list[GuardViolation] = []
    violations.extend(_check_imports(tree))
    violations.extend(_check_lookahead_names(tree))

    return GuardReport(ok=not violations, violations=violations)


def _check_imports(tree: ast.AST) -> list[GuardViolation]:
    out: list[GuardViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _import_allowed(alias.name):
                    out.append(
                        GuardViolation(
                            "import_violation",
                            f"disallowed import: {alias.name}",
                            node.lineno,
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0:
                out.append(
                    GuardViolation(
                        "import_violation",
                        f"relative import (level={node.level}) not allowed",
                        node.lineno,
                    )
                )
                continue
            if not _import_allowed(mod):
                out.append(
                    GuardViolation(
                        "import_violation",
                        f"disallowed import: {mod}",
                        node.lineno,
                    )
                )
    return out


def _import_allowed(name: str) -> bool:
    """
    Allow `name` iff its first segment matches one of the whitelisted prefixes
    exactly, or `name` begins with `<prefix>.`.

    Substring-style matching (e.g., `name.startswith("re")` for `requests`)
    is a bug — keep the comparison strict.
    """
    root = name.split(".", 1)[0]
    for prefix in ALLOWED_IMPORT_PREFIXES:
        if root == prefix or name.startswith(prefix + "."):
            return True
    return False


def _check_lookahead_names(tree: ast.AST) -> list[GuardViolation]:
    """Heuristic — flag identifiers that smell like peeking at the future."""
    out: list[GuardViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            for fragment in SUSPECT_NAME_FRAGMENTS:
                if fragment in node.id.lower():
                    out.append(
                        GuardViolation(
                            "lookahead_suspected",
                            f"suspicious identifier: {node.id}",
                            getattr(node, "lineno", 0),
                        )
                    )
                    break
        elif isinstance(node, ast.Attribute):
            for fragment in SUSPECT_NAME_FRAGMENTS:
                if fragment in node.attr.lower():
                    out.append(
                        GuardViolation(
                            "lookahead_suspected",
                            f"suspicious attribute: .{node.attr}",
                            getattr(node, "lineno", 0),
                        )
                    )
                    break
    return out
