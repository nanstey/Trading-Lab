# Runbook: Assign canonical thesis name + slug

**You are the naming agent.** You take ONE `MEMO_READY` ingestion row
and assign:

- `thesis_name` — readable strategy title (sentence case)
- `thesis_slug` — stable, mechanism-descriptive, kebab-case identity
- optional `codename` — catchy short label

The capture slug (derived from the source title) stays as provenance.
This runbook formalizes the transition from "raw source title" to
"canonical strategy identity."

## Pre-conditions

```bash
.venv/bin/python scripts/ingestion_status.py show --slug <slug>
cat research/hypotheses/<slug>/memo.md
```

The memo must be filled (you should see filled `## Polymarket fit`,
`## Source-to-binary mapping`, etc. — not `TODO`).

## Naming guidance

A good `thesis_slug`:

- **Describes the mechanism**, not the source headline. `open-range-break-bounce`
  beats `this-simple-scalping-strategy-makes-me-over-1000`.
- **Hints at venue when relevant**: `polymarket-noise-print-snapback`,
  `binary-regime-filter`. Drop the venue when it doesn't disambiguate.
- **Short** enough to live as a Python module name (≤40 chars). Allowed
  characters: `[a-z0-9-]`.
- **Reusable across multiple related sources** — if two different
  YouTube videos describe the same mechanism, they should converge on
  the same `thesis_slug`. Check existing slugs first:

  ```bash
  ls research/hypotheses/
  ```

Bad names:

- `this-simple-scalping-strategy-makes-me-over-1000` — marketing copy
- `cool-idea` — undescriptive
- `noise-print-snapback-v2` — versioning isn't identity

Catchy `codename` is allowed but never the primary slug. Use it only
when the mechanism name is wordy and a memorable short form helps the
operator scan the lifecycle DB.

## Apply

```bash
.venv/bin/python scripts/assign_thesis_name.py \
  --slug <capture_slug> \
  --thesis-name "Noise Print Snapback on Polymarket Binaries" \
  --thesis-slug noise-print-snapback \
  --codename Rubberband
```

This renames `research/hypotheses/<capture_slug>/` →
`research/hypotheses/<thesis_slug>/`, sweeps every `*.md` frontmatter
in the folder, and updates the ingestion row.

## Hard rules

- Do not reuse the raw YouTube/RSS title as the canonical slug.
- Do not rename to a slug that already exists in `research/hypotheses/`.
- Prefer mechanism-descriptive names over catchy-but-vague names.
- Preserve provenance via the unchanged `capture_slug` and
  `source_title` frontmatter fields.

## Output

The new `thesis_slug` + path. Hand off to `specify-hypothesis.md`.
