"""Hypothesis-spec validation rules.

A spec is the codegen-ready artifact at the end of the ingestion middle.
This module is the single source of truth for "what counts as a spec".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Hypothesis",
    "Market criteria",
    "Signal definition",
    "Entry rule",
    "Exit rule",
    "Sizing rule",
    "Risk controls",
    "Required data",
    "Parameter space",
    "Acceptance criteria",
)


@dataclass(frozen=True)
class SpecValidation:
    is_valid: bool
    missing_sections: tuple[str, ...]
    empty_sections: tuple[str, ...]

    @property
    def reason(self) -> str:
        bits: list[str] = []
        if self.missing_sections:
            bits.append(f"missing sections: {', '.join(self.missing_sections)}")
        if self.empty_sections:
            bits.append(f"empty sections: {', '.join(self.empty_sections)}")
        return "; ".join(bits) if bits else "ok"


_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$", re.MULTILINE)


def _section_bodies(markdown: str) -> dict[str, str]:
    """Return a dict of section heading title -> body text (until next heading of equal-or-higher level)."""
    matches = list(_HEADING_RE.finditer(markdown))
    if not matches:
        return {}
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group("title").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        out[title.lower()] = body
    return out


def validate_spec_markdown(markdown: str) -> SpecValidation:
    """Validate that the markdown contains all required spec sections with non-empty bodies."""
    bodies = _section_bodies(markdown)
    missing: list[str] = []
    empty: list[str] = []
    for section in REQUIRED_SECTIONS:
        body = bodies.get(section.lower())
        if body is None:
            missing.append(section)
            continue
        if not body or body.startswith("TODO") or body.startswith("(") or len(body) < 5:
            empty.append(section)
    return SpecValidation(
        is_valid=not missing and not empty,
        missing_sections=tuple(missing),
        empty_sections=tuple(empty),
    )
