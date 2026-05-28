# Refine Strategy Ingestion Middle — Implementation Plan

> For Hermes: use the writing-plans skill and preserve canonical discovery ownership of `PROPOSED`.

Goal: turn noisy source captures into explicit, Polymarket-testable strategy specs before codegen, without bypassing the existing lifecycle controls.

Architecture: a single linear ingestion path with two on-disk stops. URLs and feed items enter via `captures/raw/<source>/<date>/<hash>.json` (the dropbox); the rest of the lifecycle accumulates inside `hypotheses/<slug>/` (dossier → memo → spec). No parallel inboxes. Stage and queue semantics live in two new tables (`ingestion_items`, `ingestion_events`) alongside the existing strategy tables in the lifecycle DB — same file, separate tables. Discovery promotes a folder in place once its `spec.md` is valid; no separate canonical file is written.

Tech stack: existing Python scripts in `scripts/` and `src/trading_lab/agent/`, markdown artifacts under `research/`, existing lifecycle/discovery helpers, pytest.

---

## Why this refactor is needed

Current state in repo:
- `source_capture.py` archives full raw source payloads under `research/captures/raw/...`.
- `item_to_candidate()` truncates extracted evidence to 2400 chars before writing `research/manual_inbox/*.md`.
- `discover_strategies.py` drains `research/manual_inbox/` straight into `research/hypotheses/*.md` and DB state `PROPOSED`.
- Result: most `PROPOSED` hypotheses are still source summaries, not implementation-ready rule specs.

Observed failure mode:
- YouTube raw capture JSON contains the full transcript in `content`.
- The inbox artifact intentionally keeps only an excerpt, so downstream review sees a partial transcript and a weak strategy spec.
- `PROPOSED` is overloaded: it sometimes means “captured idea”, sometimes “rule-complete hypothesis”, and sometimes “already implemented strategy”.

Risk:
- Codegen sees under-specified hypotheses.
- Manual overrides become tempting.
- The evidence trail and the codified hypothesis drift apart.

---

## Design constraints

1. Preserve `discover_strategies.py` as the canonical writer into `PROPOSED`.
2. Do not create direct DB writes from source capture.
3. Preserve human gates for `PAPER_READY -> PAPER` and `LIVE_READY -> LIVE`.
4. Keep full provenance in `research/captures/`.
5. Prefer file-based middle stages before adding new lifecycle DB states.

---

## Proposed middle pipeline

### Stage 0 — Raw Capture

Input:
- YouTube URLs, RSS posts, arXiv links, operator dropbox submissions

Artifact:
- `research/captures/raw/<source_type>/<YYYY-MM-DD>/<hash>.json`

Exit criteria:
- Full original source payload archived
- Metadata captured: source URL, publish timestamp, source type, title, summary, full fetched content

Next step:
- Generate a normalized dossier from the raw artifact

Notes:
- For YouTube, this stage should always preserve the full transcript when available.
- The current code already does this in the raw JSON.

### Stage 1 — Source Dossier

Purpose:
- Create a reviewer-friendly evidence packet without throwing away important source material.

New artifact:
- `research/hypotheses/<slug>/dossier.md`

Required sections:
- Source metadata
- Full abstract/description
- Full transcript or full extracted text
- Evidence snippets with timestamps if available
- Raw capture path
- Initial relevance tags
- Known venue mismatch notes, if any

Exit criteria:
- A human or agent can review the full source in one repo-local file
- No important source content is hidden behind a 2400-char excerpt

Next step:
- Distill the dossier into a Trading-Lab-native idea memo

Implementation note:
- Keep `manual_inbox` concise if desired, but point it to the dossier and raw capture.
- Better: stop using `manual_inbox` as the first normalized artifact and introduce a dedicated dossier stage first.

### Stage 2 — Distilled Idea Memo

Purpose:
- Convert generic source material into a venue-aware research note.

New artifact:
- `research/hypotheses/<slug>/memo.md`

Required sections:
- Claimed edge
- Why this might exist on Polymarket specifically
- Why this might fail on Polymarket specifically
- Required observables/data
- Execution assumptions
- Mapping from source concept to binary-market implementation
- Fast reject reasons
- Recommended disposition: reject, shelve, or promote to hypothesis spec

Exit criteria:
- The source has been translated from content marketing / paper language into trading-lab language
- Generic cross-asset ideas that do not map to Polymarket get rejected here, before they become hypotheses

Next step:
- If still viable, write a codifiable hypothesis spec

### Stage 3 — Hypothesis Spec

Purpose:
- Produce the first artifact that is truly ready for codegen.

New artifact:
- `research/hypotheses/<slug>/spec.md`

Required sections:
- Hypothesis
- Market criteria
- Signal definition
- Entry rule
- Exit rule
- Sizing rule
- Risk controls
- Required data
- Parameter space
- Acceptance criteria
- Invalidating assumptions
- Parent source artifacts (`dossier`, `idea_memo`, `raw_capture_path`)

Exit criteria:
- Another agent could implement the strategy without guessing at the actual trading rules
- Missing fields are explicit; no “fill in the blanks from transcript” behavior

Next step:
- Canonical discovery registers this into `research/hypotheses/` + DB state `PROPOSED`

### Stage 4 — Canonical PROPOSED Hypothesis

No change in ownership:
- `scripts/discover_strategies.py` remains the canonical writer to `PROPOSED`

Change in intake contract:
- Discovery should register only folders whose `spec.md` validates, not raw source summaries

Canonical artifact:
- `research/hypotheses/<slug>/spec.md` is promoted in place — presence of a valid `spec.md` plus DB state `DISCOVERED` defines `PROPOSED`. Discovery writes no new file; it inserts the lifecycle DB row and flips the ingestion row to `DISCOVERED/DONE`.

Exit criteria:
- Hypothesis is now codegen-eligible
- Strategy module/class/config are still empty at this point, but the rule spec is complete

Next step:
- Existing `codegen-strategy.md` runbook

---

## Recommended folder layout

Folder-per-idea: every captured idea owns a single directory that accumulates artifacts as it moves through stages. Stage is tracked in the DB, not in the path — no `new/reviewing/accepted/rejected/` subdirectories. Trying to encode stage in path means physically moving folders, which breaks stable links and event history; the DB is the right place for state.

```text
research/
  captures/
    raw/<source_type>/<YYYY-MM-DD>/<hash>.json
  hypotheses/
    <slug>/
      dossier.md
      memo.md
      spec.md
      events.log      # optional offline mirror of ingestion_events
  manual_inbox/
    # optional thin operator dropbox; not the source of truth
```

Recommended role of each location:
- `captures/raw/`: immutable source evidence, never mutated, referenced by frontmatter
- `hypotheses/<slug>/`: full lifecycle of one idea — dossier, memo, spec all colocated
- `hypotheses/<slug>/spec.md`: canonical codegen-ready artifact; presence + DB state `DISCOVERED` defines `PROPOSED`
- `manual_inbox/`: operator dropbox / staging queue, not the final source of truth for captured strategy content

Slug lifecycle within the folder:
- folder is created with the provisional `capture_slug`
- at the naming checkpoint the folder is renamed once to the canonical `thesis_slug` (`git mv` + DB update + frontmatter sweep)
- the canonical `thesis_slug` is the stable identity for the rest of the strategy lifecycle
- rejected/shelved ideas keep their folder in place; DB stage marks them terminal

---

## Tracking model

The lifecycle DB grows two new ingestion tables alongside the existing strategy tables. One database, separate tables — ingestion semantics are distinct from strategy lifecycle, but a separate DB file buys nothing and costs an extra backup target plus FK gymnastics. Writer separation is enforced by code: `discover_strategies.py` remains the only process that writes to the strategy lifecycle table.

### Recommended tracking store

Extend the existing lifecycle DB with two new tables. No separate DB file.

Recommended tables:

1. `ingestion_items`
- one row per captured idea lineage
- stable primary key: `intake_id`
- tracks current upstream stage and current next action

Suggested fields:
- `intake_id` — stable PK (uuid or autoinc); survives slug renames
- `source_url`
- `source_type`
- `source_title`
- `capture_slug` — provisional slug derived from headline/video title
- `thesis_name` — human-readable strategy name assigned later
- `thesis_slug` — canonical strategy slug assigned at naming checkpoint; folder is renamed to match
- `folder_path` — `research/hypotheses/<current-slug>/`, single column instead of per-artifact paths (all artifacts are colocated and discoverable by convention: `dossier.md`, `memo.md`, `spec.md`)
- `raw_capture_path` — points into immutable `research/captures/raw/...`
- `stage` — `CAPTURED | DOSSIER_READY | MEMO_READY | SPEC_READY | DISCOVERED | REJECTED_SOURCE | SHELVED_SOURCE`
- `status` — `PENDING | IN_PROGRESS | BLOCKED | DONE`
- `next_action`
- `notes`
- `created_at`
- `updated_at`

Once a row reaches `DISCOVERED`, `thesis_slug` becomes the join key to the strategy lifecycle table — provenance is queryable in a single join without crossing DB files.

2. `ingestion_events`
- append-only event log for auditability
- fields: `intake_id`, `timestamp`, `actor`, `from_stage`, `to_stage`, `action`, `details_json`

Why separate tables (but the same DB):
- ingestion has its own state machine (`CAPTURED → DOSSIER_READY → ...`) distinct from strategy lifecycle (`PROPOSED → PAPER → LIVE`); tables not schemas
- one source item may die before becoming a hypothesis; many source items may converge into one strategy
- `ingestion_items.thesis_slug` FKs to the strategy table once promoted, keeping provenance queryable in one join
- single DB file means one backup, one connection pool, one migration story

### Artifact-level tracking fields

Every dossier / memo / spec should repeat a small set of frontmatter fields so the files are self-describing:
- `artifact_type: dossier|idea_memo|hypothesis_spec`
- `intake_id: <stable-id>`
- `capture_slug: <headline-derived-provisional-slug>`
- `thesis_name: <empty-until-assigned>`
- `thesis_slug: <empty-until-assigned>`
- `source_title:`
- `source_url:`
- `raw_capture_path:`
- `upstream_artifact:`
- `recommended_next_action:`

This gives you two views:
- repo-local file view for humans
- DB queue/status view for automation

### Queue semantics

Use stage + status to drive obvious next steps:

- `CAPTURED/PENDING` -> build dossier
- `DOSSIER_READY/PENDING` -> distill idea memo
- `MEMO_READY/PENDING` -> assign thesis name + write hypothesis spec
- `SPEC_READY/PENDING` -> validate and discover into `PROPOSED`
- `DISCOVERED/DONE` -> hand off to lifecycle DB and downstream loop
- `REJECTED_SOURCE/DONE` -> stop upstream processing, preserve provenance
- `SHELVED_SOURCE/DONE` -> stop automatic advancement, preserve for later revisit

This is the missing tracking layer between source capture and lifecycle registration.

---

## Naming policy — provisional slugs vs canonical strategy names

The source headline or YouTube title is fine for initial provenance, but it is the wrong long-term identity for a strategy.

### Recommended rule

1. **Capture stage uses a provisional `capture_slug`**
- derived from title/headline/video ID
- purpose: easy file creation and provenance linking
- examples:
  - `this-simple-scalping-strategy-makes-me-over-1000`
  - `hidden-markov-models-for-quant-finance`

2. **Spec stage assigns the canonical `thesis_name` and `thesis_slug`**
- this is the first point where the idea should be concrete enough to deserve a strategy identity
- this name should describe the actual edge, not the source marketing copy

3. **Discovery registers only the canonical `thesis_slug` into `research/hypotheses/` and the lifecycle DB**
- `capture_slug` remains in provenance fields
- the hypothesis slug should be stable, meaningful, and re-usable across multiple related sources

### Naming guidance

Canonical strategy identity should be:
- descriptive of mechanism
- venue-aware where relevant
- short enough for filenames / strategy modules
- not tied to a single content creator's title

Good examples:
- `open-range-break-bounce`
- `noise-print-snapback`
- `spread-refill-fade`
- `binary-regime-filter`

Bad examples:
- `this-simple-scalping-strategy-makes-me-over-1000`
- `how-trading-like-an-idiot-makes-me-10000month-15`

### Catchy naming policy

If you want something memorable, separate the machine identity from the display identity:
- `thesis_slug`: stable, descriptive, operational
- `thesis_name`: readable title
- optional `codename`: catchy short label

Example:
- `thesis_slug: noise-print-snapback`
- `thesis_name: Noise Print Snapback on Polymarket Binaries`
- `codename: Rubberband`

Recommendation:
- use descriptive `thesis_slug`
- allow a catchy `codename` only as a secondary field
- do not use codenames as the primary lifecycle slug unless they remain self-explanatory

### Required naming step

Add an explicit naming checkpoint between idea memo and hypothesis spec:
- outcome: `thesis_name`, `thesis_slug`, optional `codename`
- if the idea cannot be named clearly, it usually is not specified clearly enough yet

---

## Required tooling changes

### 1. New script: `scripts/build_source_dossier.py`

Input:
- raw capture path or source URL

Output:
- `research/hypotheses/<slug>/dossier.md` (creates the folder if needed)
- inserts a row into `ingestion_items` at stage `CAPTURED` if not already present, then flips to `DOSSIER_READY/PENDING`

Behavior:
- Expand raw capture into a readable, full-content dossier
- Include full YouTube transcript, not just excerpt
- Preserve raw capture path and source metadata

Why:
- Fixes the current “YouTube transcript only shows excerpt” review problem without bloating the DB summary field

### 2. New script: `scripts/distill_source_material.py`

Input:
- dossier path or `--slug`

Output:
- `research/hypotheses/<slug>/memo.md`
- flips ingestion row to `MEMO_READY/PENDING`

Behavior:
- Convert source material into a venue-aware trading memo
- Force explicit statements of edge, venue fit, and failure modes

### 3. New script: `scripts/write_hypothesis_spec.py`

Input:
- idea memo path or `--slug`

Output:
- `research/hypotheses/<slug>/spec.md`
- flips ingestion row to `SPEC_READY/PENDING`

Behavior:
- Populate a strict template for codegen-ready specs
- Refuse to complete if entry/exit/sizing/risk/data/parameter space are missing

### 4. Update `scripts/discover_strategies.py`

Current behavior:
- drains `research/manual_inbox/` directly to `PROPOSED`

Recommended behavior:
- switch discovery input to the ingestion queue: select ingestion rows at `SPEC_READY/PENDING` and validate `research/hypotheses/<slug>/spec.md`
- promote in place: insert a row in the strategy lifecycle table keyed by `thesis_slug`, then flip the ingestion row to `DISCOVERED/DONE`
- no new artifact is written; the folder + valid `spec.md` is the canonical hypothesis

### 5. Optional new verifier: `scripts/validate_hypothesis_spec.py`

Checks:
- required sections present
- parameter space parsable
- market criteria present
- links to parent artifacts present
- no empty trading rules

### 6. New tracker CLI: `scripts/ingestion_status.py`

Purpose:
- inspect the upstream queue without mixing it into `research_cli.py`

Suggested commands:
- `scripts/ingestion_status.py list`
- `scripts/ingestion_status.py show --intake-id <id>`
- `scripts/ingestion_status.py next --stage DOSSIER_READY`
- `scripts/ingestion_status.py stale --older-than 3d`

Why:
- operators need a clean answer to “what is waiting at each stage?”
- cron jobs need deterministic queue selection

### 7. New naming CLI: `scripts/assign_thesis_name.py`

Purpose:
- formalize the transition from provisional source slug to canonical strategy identity

Input:
- intake ID or idea memo path

Output:
- updates ingestion tracker
- writes `thesis_name`, `thesis_slug`, optional `codename` into memo/spec frontmatter

Why:
- naming should be an explicit controlled step, not an accidental carry-over from a YouTube title

---

## Required processes

There are two process classes here: mechanical transforms and judgment-heavy transforms.

### Mechanical processes

These can be automated aggressively:
- raw source capture
- archive writes
- dossier rendering from raw captures
- spec validation
- discovery registration of already-approved hypothesis specs

### Judgment-heavy processes

These should initially run as queued single-item jobs, not broad parallel floods:
- distilling a dossier into a venue-aware idea memo
- deciding whether the idea is actually Polymarket-implementable
- assigning canonical thesis name / slug
- writing the final hypothesis spec when the source is vague or under-specified

### Recommended operating rule

For v1:
- automate mechanical stages fully
- let agentic crons process at most one oldest pending memo/spec task per run
- keep source rejection and naming visible in the tracker

This reduces garbage-in to codegen.

---

## Required cron topology

Current cron inventory already includes these repo-level jobs:
- `trading-lab-research-capture`
- `trading-lab-link-dropbox` — **retired** (link_dropbox folder is removed; Telegram bot calls `ingest_link.py` directly)
- `trading-lab-research-discover-daily`
- downstream test / optimize / paper summary / paper watcher jobs

Do not duplicate those blindly. Update the ingestion-side jobs to match the refined middle pipeline.

### Recommended upstream cron stack

1. `trading-lab-research-capture`
- cadence: every 6h
- job: poll enabled public sources and archive raw captures; insert `ingestion_items` rows at `CAPTURED/PENDING` for each new capture
- output: `[SILENT]` on no-op, terse count on new captures

2. New: `trading-lab-build-dossiers`
- cadence: every 15m or every 30m
- queue: oldest `CAPTURED/PENDING` in the `ingestion_items` table
- job: materialize full-content dossier from raw capture
- output example: `dossier: 1 ready (noise-print-snapback-src)`

3. New: `trading-lab-distill-ideas`
- cadence: every 6h
- queue: oldest `DOSSIER_READY/PENDING`
- job: produce one idea memo and update tracker
- output example: `distill: 1 memo ready (noise-print-snapback-src)`

4. New: `trading-lab-specify-hypotheses`
- cadence: every 6h, offset from distill cron
- queue: oldest `MEMO_READY/PENDING`
- job: assign thesis name/slug, write hypothesis spec, validate it
- output example: `spec: 1 ready (noise-print-snapback)`

5. Update: `trading-lab-research-discover-daily`
- preferred new cadence: every 6h once spec stage exists
- intake source: spec-grade artifacts only
- queue: oldest `SPEC_READY/PENDING`
- output example: `discover: 1 new (noise-print-snapback)`

### Recommended timing offsets

Example:
- `00 */6 * * *` capture
- `15 */6 * * *` dossier build
- `30 */6 * * *` distill
- `45 */6 * * *` specify + validate
- `0 1-23/6 * * *` discover

The key is ordering, not exact minutes:
- capture before dossier
- dossier before distill
- distill before spec
- spec before discover

### Cron prompt rules for the new middle jobs

Each job should:
- operate on exactly one queued intake item unless the stage is purely mechanical and cheap
- use repo workdir `/home/nautilus/code/Trading-Lab`
- verify the expected artifact path was actually written
- update the `ingestion_items` table
- emit `[SILENT]` on true no-op
- only the existing discovery job writes to the strategy lifecycle table; the new middle jobs may write only to the ingestion tables

### Queue selectors

For the ingestion DB, define deterministic selectors like:
- oldest `CAPTURED/PENDING`
- oldest `DOSSIER_READY/PENDING`
- oldest `MEMO_READY/PENDING`
- oldest `SPEC_READY/PENDING`

This avoids the current ambiguity where queue selection is inferred indirectly from hypothesis ordering.

---

## Required runbooks

### A. `runbooks/review-source-dossier.md`

Agent role:
- inspect a dossier and decide whether the source has enough substance to justify distillation

Outputs:
- `PASS_TO_IDEA_MEMO`
- `REJECT_SOURCE`
- `NEEDS_HUMAN_REVIEW`

### B. `runbooks/distill-idea.md`

Agent role:
- turn full source content into a Polymarket-native idea memo

Hard rules:
- do not invent data we do not have
- do not claim venue fit without explaining the mapping
- surface failure modes before upside

### C. `runbooks/specify-hypothesis.md`

Agent role:
- convert an idea memo into a codifiable hypothesis spec

Hard rules:
- no vague “use indicators” language
- entry, exit, sizing, and risk controls must be explicit
- if the idea cannot be made concrete, stop and reject or shelve upstream

### D. `runbooks/name-strategy.md`

Agent role:
- assign the canonical strategy identity before hypothesis registration

Outputs:
- `thesis_name`
- `thesis_slug`
- optional `codename`

Hard rules:
- do not reuse the raw YouTube title as the canonical strategy slug
- prefer mechanism-descriptive names over catchy-but-vague names
- preserve provenance via `capture_slug` and `source_title`

### E. Existing runbooks remain downstream

No ownership change for:
- `runbooks/codegen-strategy.md`
- `runbooks/test-strategy.md`
- `runbooks/optimize-strategy.md`

## Transcript handling fix

Current code issue:
- `src/trading_lab/agent/source_capture.py:item_to_candidate()` truncates evidence to 2400 chars before the inbox artifact is written.
- This is why downstream human review sees only an excerpt even though the raw archive contains the full transcript.

Recommended fix:
1. Keep the excerpt for compact previews if desired.
2. Add a full-content dossier artifact as the review surface.
3. Add explicit metadata fields in candidate/inbox/spec artifacts:
   - `raw_capture_path`
   - `dossier_path`
   - `transcript_available: true|false`
   - `transcript_format: full|excerpt`
4. Do not rely on `summary` or `excerpt` alone for YouTube-source review.

Optional near-term patch:
- during capture for YouTube URLs only, create `research/hypotheses/<capture_slug>/dossier.md` with the full transcript even before the full middle-stage refactor lands.

---

## Existing-folder reorganization

### Current state of `research/`

```text
research/
  captures/raw/{arxiv,rss,youtube}/...      # immutable source archive (steady)
  hypotheses/*.md                            # 24 flat-file hypotheses, mixed quality
  link_dropbox/                              # file-based URL inbox (to be retired)
  manual_inbox/*.md                          # normalized candidates awaiting discovery (to be retired)
  paper_reports/<slug>_<YYYYMMDD>.md         # daily paper-trading reports per strategy
  experiments.db                             # existing lifecycle DB
  sources.yaml                               # enabled feed config
```

### What merges, what stays

| Existing | Disposition |
|---|---|
| `hypotheses/<slug>.md` | Migrate into `hypotheses/<slug>/`. Spec-quality content becomes `spec.md` and gets an ingestion row at `DISCOVERED/DONE`. Source-summary content becomes `dossier.md` and gets a row at `DOSSIER_READY/PENDING` — under-specified entries should not retain a `PROPOSED` lifecycle row after migration. |
| `manual_inbox/<slug>.md` | Becomes `hypotheses/<slug>/dossier.md` (or `memo.md` if already venue-aware). Ingestion row inserted at the matching stage. Folder is deleted once empty — the ingestion DB queue replaces it. |
| `captures/raw/...` | **Stays put, promoted to sole entry point.** Immutable, source-organized archive AND the dropbox where every URL/feed item lands first. Per-strategy folders reference it via `raw_capture_path` frontmatter. |
| `link_dropbox/` | **Retired.** It was a file-based workaround for callers that couldn't invoke Python (Telegram bot, manual `echo > file`). Telegram bot updated to call `scripts/ingest_link.py` directly, which already does URL → `captures/raw/...` without the intermediate text file. Existing `.archived/` history can be left in place or deleted; the actual URL record lives in `captures/raw/.../<hash>.json` either way. |
| `paper_reports/<slug>_<YYYYMMDD>.md` | Move into `hypotheses/<slug>/paper_reports/<YYYYMMDD>.md`. Filename loses the slug prefix since the folder name carries identity. |
| `experiments.db` | Untouched on disk. Add `ingestion_items` + `ingestion_events` tables via migration; no schema changes to existing strategy tables. |
| `sources.yaml` | Untouched. |

Net effect: a single ingestion path. URLs land in `captures/raw/`, then the next stop is `hypotheses/<slug>/`. No parallel inboxes (`link_dropbox/`, `manual_inbox/`) to track.

### Per-strategy artifacts beyond the spec

Once we commit to folder-per-idea, paper reports, backtests, and optimization runs all colocate under the strategy folder. Operator-facing "today's reports across all strategies" becomes a CLI concern, not a filesystem layout concern.

```text
hypotheses/
  tick-mean-revert/
    dossier.md
    memo.md
    spec.md
    paper_reports/
      20260525.md
      20260526.md
      20260527.md
    backtests/         # future
    optimizations/     # future
    events.log         # optional offline mirror of ingestion_events
```

### Target shape after migration

```text
research/
  captures/raw/{arxiv,rss,youtube,manual,telegram}/...   # sole ingestion dropbox
  hypotheses/
    <slug>/
      dossier.md
      memo.md
      spec.md
      paper_reports/<YYYYMMDD>.md
  experiments.db                                          # + ingestion_items, + ingestion_events
  sources.yaml                                            # unchanged
```

Removed: `research/manual_inbox/`, `research/paper_reports/`, `research/link_dropbox/`.

### Migration steps

1. **DB migration**: add `ingestion_items` and `ingestion_events` tables to `research/experiments.db`. Empty schema; no data yet. Reversible by dropping tables.
2. **Build the migration script** `scripts/migrate_research_layout.py` with a dry-run default and an explicit `--apply` flag:
   - For each `research/hypotheses/<slug>.md`, classify by frontmatter/content quality:
     - **spec-quality** (entry/exit/sizing/risk/data/parameter space all present): `git mv` to `research/hypotheses/<slug>/spec.md`, insert ingestion row at `DISCOVERED/DONE`, leave existing lifecycle row alone.
     - **source-summary quality**: `git mv` to `research/hypotheses/<slug>/dossier.md`, insert ingestion row at `DOSSIER_READY/PENDING`, remove the lifecycle row (was incorrectly `PROPOSED`).
     - **manual override / unclear**: do not move; print to triage list and require human decision.
   - For each `research/manual_inbox/<slug>.md`: `git mv` to `research/hypotheses/<slug>/dossier.md`, insert ingestion row at `DOSSIER_READY/PENDING`.
   - For each `research/paper_reports/<slug>_<YYYYMMDD>.md`: `git mv` to `research/hypotheses/<slug>/paper_reports/<YYYYMMDD>.md`.
   - Walk `research/captures/raw/.../<hash>.json` and backfill `raw_capture_path` on ingestion rows where source URL matches.
   - Print a classification table summarizing all moves, ingestion rows inserted, and lifecycle rows removed before applying.
3. **Use `git mv` throughout** to preserve history. Single commit per migration phase so the change is reviewable and revertable.
4. **Delete** `research/manual_inbox/` and `research/paper_reports/` once empty.
5. **Update writers**:
   - `scripts/discover_strategies.py`: switch intake from `manual_inbox/` to the ingestion queue (`SPEC_READY/PENDING`).
   - Paper report cron writer: emit to `research/hypotheses/<slug>/paper_reports/<YYYYMMDD>.md`.
   - Capture flow: write dossier directly into `research/hypotheses/<slug>/dossier.md`.
6. **Retire `link_dropbox/`**:
   - Update the Telegram bot to call `scripts/ingest_link.py` (or `source_capture.capture_url()`) directly instead of writing `.txt` files.
   - Delete `scripts/process_link_dropbox.py` and `scripts/drop_youtube_link.py`.
   - Remove `DEFAULT_LINK_DROPBOX` and `process_link_dropbox()` from [`src/trading_lab/agent/source_capture.py`](src/trading_lab/agent/source_capture.py).
   - Remove the `trading-lab-link-dropbox` cron entry.
   - `git rm -r research/link_dropbox/`.
7. **Verify**: `scripts/ingestion_status.py list` row counts match folder counts; Telegram bot still produces captures; existing paper-report cron still produces files at the new path.

### Backfill classification of current `hypotheses/`

The 24 existing flat-file hypotheses span several quality tiers:
- **Likely spec-quality**: `tick-mean-revert` (has real paper reports, implies a working spec)
- **Likely source-summary quality**: `this-simple-scalping-strategy-makes-me-over-1000`, `how-trading-like-an-idiot-makes-me-10000month-15`, `i-backtested-rsi-trading-strategy-for-6-years-60`, the various `recent-quant-links-from-quantocracy-*` items
- **Unclear / requires triage**: `wide-spread-fade`, `for-the-love-of-the-game`, `the-metamorphosis`, anything where the file is a paper abstract rather than a trading rule

The migration script's classification report is the source of truth — do not pre-decide these here. The script reads each file and applies the spec-validation rules from [`scripts/validate_hypothesis_spec.py`](../../scripts/validate_hypothesis_spec.py).

---

## Minimal migration path

### Phase 1 — Low-risk visibility fix
- Keep current capture and discovery working.
- Add `research/hypotheses/<slug>/dossier.md` generation for YouTube captures.
- Preserve current `manual_inbox/` behavior temporarily.
- Update inbox markdown to point to the dossier path.

Outcome:
- Full transcript becomes reviewable immediately.

### Phase 2 — Add ingestion tables + distillation layer
- Add `ingestion_items` and `ingestion_events` tables to the existing lifecycle DB.
- Introduce `research/hypotheses/<slug>/memo.md` generation and the distillation runbook/script.
- Start manually or agentically distilling the best backlog items.

Outcome:
- Raw content stops jumping straight to hypothesis registration.
- Queue state is queryable from one DB.

### Phase 3 — Add strict hypothesis spec stage
- Introduce `research/hypotheses/<slug>/spec.md` generation and spec validation.
- Change discovery to register only folders whose `spec.md` validates and whose ingestion row is `SPEC_READY/PENDING`.

Outcome:
- `PROPOSED` becomes “ready for codegen”, not “interesting source summary”.

### Phase 4 — Clean backlog and align states
- Reclassify current backlog:
  - raw captures stay captures
  - weak source summaries become dossiers or idea memos
  - only mature ones become hypothesis specs
- Audit drift cases like `wide-spread-fade` and manual overrides like `tick-mean-revert`.

Outcome:
- state semantics become consistent again.

### Phase 5 — Collapse parallel inboxes
- Update the Telegram bot to call `scripts/ingest_link.py` (or `source_capture.capture_url()`) directly.
- Delete `scripts/process_link_dropbox.py`, `scripts/drop_youtube_link.py`, the `link_dropbox` helpers in `source_capture.py`, and the `trading-lab-link-dropbox` cron entry.
- `git rm -r research/link_dropbox/`.

Outcome:
- One ingestion entry point. `captures/raw/` is the only dropbox; `hypotheses/<slug>/` is the only working folder.

---

## Suggested acceptance criteria for this refactor

1. A YouTube ingestion produces:
- raw capture JSON with full transcript
- dossier markdown with full transcript
- optional compact inbox entry with pointers to dossier/raw capture

2. No artifact enters canonical `PROPOSED` unless it includes:
- market criteria
- signal definition
- entry rule
- exit rule
- sizing rule
- risk controls
- parameter space
- acceptance criteria

3. Discovery remains the only DB writer for `PROPOSED`.

4. A reviewer can answer, from repo-local artifacts alone:
- what the source claimed
- whether it maps to Polymarket
- what exact strategy would be coded
- why it should be rejected if it does not

---

## Concrete next actions

Priority 1:
1. Implement `scripts/build_source_dossier.py` writing to `research/hypotheses/<slug>/dossier.md`
2. Patch capture flow so YouTube ingests surface full transcript via dossier
3. Add `folder_path` + `raw_capture_path` to inbox/source metadata

Priority 2:
4. Add `runbooks/distill-idea.md`
5. Add `research/hypotheses/<slug>/memo.md` template and `scripts/distill_source_material.py`
6. Add the `trading-lab-hypothesis-distillation` skill
7. Add `ingestion_items` + `ingestion_events` tables to the existing lifecycle DB; add `scripts/ingestion_status.py` reading from them

Priority 3:
8. Add `runbooks/name-strategy.md` and `scripts/assign_thesis_name.py` (handles folder rename + frontmatter sweep + DB update atomically)
9. Add `research/hypotheses/<slug>/spec.md` template and `scripts/write_hypothesis_spec.py`
10. Add `scripts/validate_hypothesis_spec.py`
11. Change `discover_strategies.py` intake contract: select from ingestion queue at `SPEC_READY/PENDING`, validate `spec.md`, promote in place

Priority 4:
12. Backfill the best existing captures through dossier -> idea memo -> naming -> hypothesis spec
13. Audit current `PROPOSED` backlog and separate source summaries from codegen-ready hypotheses
14. Update existing cron inventory so upstream jobs follow the refined stage order

---

## Initial backlog candidates to reprocess through the new middle

Good test cases:
- `this-simple-scalping-strategy-makes-me-over-1000`
- `i-backtested-rsi-trading-strategy-for-6-years-60`
- `hidden-markov-models-for-quant-finance`
- `the-privacy-subsidy-in-continuous-time-kyle-cumu`

Reason:
- they span YouTube and paper sources
- they expose the current “summary vs spec” mismatch clearly

---

## Verification commands after implementation

```bash
.venv/bin/python scripts/ingest_link.py --url "https://www.youtube.com/watch?v=xTTDH5iRhJc" --source-name test-dossier
.venv/bin/python scripts/ingestion_status.py list
.venv/bin/python scripts/build_source_dossier.py --slug this-simple-scalping-strategy-makes-me-over-1000
.venv/bin/python scripts/distill_source_material.py --slug this-simple-scalping-strategy-makes-me-over-1000
.venv/bin/python scripts/assign_thesis_name.py --slug this-simple-scalping-strategy-makes-me-over-1000
.venv/bin/python scripts/write_hypothesis_spec.py --slug this-simple-scalping-strategy-makes-me-over-1000
.venv/bin/python scripts/discover_strategies.py --dry-run
```

Expected outcomes:
- ingestion status shows the item at the correct upstream stage
- dossier exists and includes full transcript
- idea memo explicitly discusses Polymarket fit/failure
- thesis name / slug differ from the raw YouTube title when appropriate
- hypothesis spec contains concrete rules and parameter space
- discovery dry-run reports only spec-grade items as candidates
