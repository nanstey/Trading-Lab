"""Tests for hypothesis-spec validation."""

from __future__ import annotations

from trading_lab.agent.spec_validation import REQUIRED_SECTIONS, validate_spec_markdown


def _full_spec() -> str:
    parts = ["# noise-print-snapback\n"]
    for section in REQUIRED_SECTIONS:
        parts.append(f"## {section}")
        parts.append(f"Concrete content for {section} that is non-trivial and at least a sentence long.")
        parts.append("")
    return "\n".join(parts)


def test_validate_full_spec_passes():
    result = validate_spec_markdown(_full_spec())
    assert result.is_valid
    assert result.missing_sections == ()
    assert result.empty_sections == ()


def test_validate_missing_sections():
    md = "# foo\n\n## Hypothesis\nA real hypothesis sentence.\n"
    result = validate_spec_markdown(md)
    assert not result.is_valid
    assert "Market criteria" in result.missing_sections
    assert "Entry rule" in result.missing_sections


def test_validate_empty_sections():
    md_lines = ["# foo"]
    for section in REQUIRED_SECTIONS:
        md_lines.append(f"## {section}")
        md_lines.append("TODO")
        md_lines.append("")
    result = validate_spec_markdown("\n".join(md_lines))
    assert not result.is_valid
    assert set(result.empty_sections) == set(REQUIRED_SECTIONS)


def test_validate_source_summary_is_not_a_spec():
    """A typical pre-migration hypothesis file with just a summary should fail validation."""
    md = """---
slug: foo
state: PROPOSED
---

# foo

> The following summary was sourced from an external URL.

```
A YouTuber says scalping works.
```
"""
    result = validate_spec_markdown(md)
    assert not result.is_valid
    assert len(result.missing_sections) == len(REQUIRED_SECTIONS)
