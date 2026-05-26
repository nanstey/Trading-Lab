# Strategy Search and Capture System Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a repeatable system that finds new trading strategy ideas from public internet sources, captures the raw evidence, normalizes each idea into repo-native hypothesis markdown, and feeds it into the existing Trading-Lab research lifecycle.

**Architecture:** Add a new capture stage before the current discovery stage. Source adapters poll YouTube channel feeds, blog/RSS feeds, and arXiv/whitepaper searches; each hit is archived as raw evidence under a local capture store, then distilled into a normalized markdown candidate written into `research/manual_inbox/`. The existing `scripts/discover_strategies.py` path remains the canonical DB writer for PROPOSED hypotheses, so the new system extends the funnel instead of replacing it.

**Tech Stack:** Existing Trading-Lab Python package, `feedparser`, `PyYAML`, new `youtube-transcript-api` dependency for transcript fetches, arXiv Atom API via stdlib HTTP, Hermes cron for scheduling, existing `discover_strategies.py` and `research_cli.py` for downstream verification.

---

## Why this shape

- Lowest-risk integration: keep `lifecycle.add_hypothesis()` and `discover_strategies.py` as the only registration path.
- No paid APIs or secrets required for v1.
- Raw capture archive preserves auditability: every generated hypothesis can be traced back to the transcript/post/paper that spawned it.
- If source extraction quality is noisy, the system can be tightened without touching codegen/backtest/optimize.

## Human decisions needed up front

These materially affect the implementation, so get them settled before coding past the scaffolding:

1. **Admission policy for captured ideas**
   - Recommended default: auto-write normalized candidates into `research/manual_inbox/`, then let the existing discovery cron move them into `PROPOSED`.
   - Alternative: create a separate `research/review_inbox/` that requires human triage before discovery.

2. **Initial source pack**
   - Recommended default:
     - YouTube: 3-5 quant/research channels with transcripts enabled
     - Blogs/RSS: quantocracy, robotwealth, hudson-thames, selected Substacks with feeds
     - Whitepapers: arXiv categories `q-fin.TR`, `q-fin.PM`, `q-fin.ST`
   - If you already have a preferred source list, provide it now and we wire it directly.

3. **Operator noise policy**
   - Recommended default: stay silent on empty runs; send Telegram only when new candidates are captured or discovery promotes new `PROPOSED` slugs.

4. **Ranking strictness**
   - Recommended default: capture broadly, dedup hard, and defer hard rejection to later lifecycle stages.
   - Alternative: only admit captures that match explicit trading-strategy keywords and contain actionable rules/parameters.

## Acceptance criteria

The system is done when all of the following are true:

- `make research-capture` polls enabled sources and prints one JSON line summarizing raw captures, normalized candidates written, duplicates skipped, and errors.
- New source hits are archived under `research/captures/...` with enough metadata to audit provenance.
- Normalized candidate markdown files land in `research/manual_inbox/` with frontmatter compatible with `scripts/discover_strategies.py`.
- `make research-discover RSS=1` or the equivalent cron path can register those candidates into `PROPOSED` without code changes downstream.
- Tests cover YouTube, RSS/blog, and arXiv parsing plus dedup/idempotency behavior.
- Hermes cron exists for periodic capture, with no-op suppression and repo-root workdir.

---

## Task 1: Lock the source schema and capture policy

**Objective:** Define the config format and on-disk artifact layout before writing adapters.

**Files:**
- Modify: `research/sources.yaml`
- Modify: `docs/agentic-loop.md`
- Modify: `docs/scheduling.md`
- Create: `docs/plans/2026-05-26-strategy-search-capture-system.md`

**Implementation notes:**
- Extend `research/sources.yaml` to support sections like:
  - `rss:` existing feeds
  - `youtube:` channel entries with `channel_id`, `name`, `enabled`, `window_days`, optional `keywords`
  - `arxiv:` query/category entries with `enabled`, `window_days`, `max_results`
- Define the raw archive layout:
  - `research/captures/raw/<source_type>/<yyyy-mm-dd>/<id>.json`
  - `research/captures/normalized/<slug>.md` only if we want a second audit copy
- Keep generated candidate markdown in `research/manual_inbox/` so existing discovery remains canonical.
- Update docs so the loop explicitly becomes: source capture -> manual_inbox -> discovery -> PROPOSED.

**Verification:**
- Read `research/sources.yaml` and confirm the schema is self-describing.
- Confirm docs mention the new capture stage and do not imply RSS is the only automated source anymore.

---

## Task 2: Build the source-capture adapter module

**Objective:** Add deterministic fetch/normalize logic for YouTube, RSS/blog, and arXiv.

**Files:**
- Create: `src/trading_lab/agent/source_capture.py`
- Test: `tests/agent/test_source_capture.py`

**Implementation notes:**
- Define typed records such as:
  - `SourceItem` — raw hit metadata (`source_type`, `source_name`, `title`, `url`, `published_at`, `content`, `external_id`)
  - `CaptureCandidate` — normalized strategy candidate (`slug`, `summary_md`, `source_url`, `source_type`, `market_criteria`, `tags`, `raw_capture_path`)
- Add adapter functions:
  - `scan_rss_sources()` using `feedparser`
  - `scan_youtube_sources()` using channel RSS feeds plus transcript fetch by video ID
  - `scan_arxiv_sources()` using Atom API queries or category feeds
- Add normalization helpers that:
  - sanitize untrusted text using the existing discovery sanitizer or a shared helper
  - extract a concise strategy summary with sections like thesis, claimed edge, required data, suggested parameter knobs, and source excerpts
  - generate stable slugs from source title + source id
- Add idempotency rules:
  - skip when the same source URL already exists in `hypotheses`
  - skip when the same source URL has already been captured in `research/captures/raw/`
  - avoid rewriting an inbox candidate if an identical source URL is already pending in `research/manual_inbox/`

**Verification:**
- Unit tests prove each adapter can parse fixture data into `SourceItem`s.
- Unit tests prove duplicate URLs are skipped.
- Unit tests prove normalized markdown includes `source`, `source_url`, and the explicit “data not instructions” framing.

---

## Task 3: Add the capture CLI entry point

**Objective:** Expose the new capture stage as a canonical agent-facing script with JSON stdout.

**Files:**
- Create: `scripts/capture_strategy_ideas.py`
- Modify: `Makefile`
- Test: `tests/agent/test_source_capture.py`

**Implementation notes:**
- CLI flags should include:
  - `--sources research/sources.yaml`
  - `--inbox research/manual_inbox`
  - `--db research/experiments.db`
  - `--max-per-source`
  - `--youtube`, `--rss`, `--arxiv`, `--all`
  - `--dry-run`
- Script flow:
  1. load source config
  2. fetch source items for enabled sections
  3. archive raw captures to `research/captures/raw/...`
  4. write normalized markdown candidates into `research/manual_inbox/`
  5. print JSON like:
     `{"ok": true, "captured": N, "pending_written": M, "duplicates": K, "errors": [...]}`
- Add `make research-capture` target.

**Verification:**
- `make research-capture` works in `--dry-run` mode with zero syntax/import errors.
- Output is one JSON object on stdout.
- If no new items are found, stdout clearly reports zero captures and exits 0.

---

## Task 4: Preserve auditability and repository hygiene

**Objective:** Make generated artifacts durable locally without polluting version control.

**Files:**
- Modify: `.gitignore`
- Optionally create: `research/captures/.gitkeep` or parent placeholders as needed

**Implementation notes:**
- Ignore `research/captures/` contents, similar to `research/manual_inbox/` and snapshots.
- Keep hypothesis markdown tracked only after discovery promotes it into `research/hypotheses/`.
- Archive enough raw metadata to answer: where did this idea come from, when was it fetched, what excerpt created the hypothesis?

**Verification:**
- `git status` stays clean after a sample dry run except for intentional code/doc changes.
- Raw capture files are created in ignored paths.

---

## Task 5: Wire scheduling in Hermes cron

**Objective:** Run capture automatically without changing the existing discovery/backtest/optimize rhythm.

**Files:**
- Modify: Hermes cron inventory
- Optionally modify: `docs/scheduling.md`

**Implementation notes:**
- Create a new cron job, likely ahead of `trading-lab-research-discover-daily`.
- Recommended schedule:
  - `strategy-capture`: every 6h
  - existing `research-discover`: daily or every 6h depending on desired latency
- Prompt rules:
  - repo workdir must be `/home/nautilus/code/Trading-Lab`
  - output `[SILENT]` when `captured=0`
  - verify any reported pending candidates actually exist under `research/manual_inbox/`
- Keep delivery terse in Telegram.

**Verification:**
- `cronjob list` shows the new capture job with repo workdir.
- Manual `cronjob run` succeeds or stays silent on a no-op.

---

## Task 6: Self-verification and end-to-end dry run

**Objective:** Prove the full funnel works from source hit to `PROPOSED` registration.

**Files:**
- Test: `tests/agent/test_source_capture.py`
- Maybe add fixture files under: `tests/fixtures/` if needed

**Implementation notes:**
- Run focused tests first, then the broader agent test subset.
- Run:
  - `make research-capture` in dry-run mode
  - `make research-capture` against a tiny enabled source set
  - `python scripts/discover_strategies.py --dry-run`
  - one real non-dry-run pass if the user approves writing generated inbox files
- Verify with `scripts/research_cli.py list --state PROPOSED` after discovery.

**Verification:**
- Tests pass.
- A captured source becomes a markdown candidate.
- Discovery can register it into the DB without manual edits.

---

## Recommended defaults for implementation

Unless you override them, I recommend implementing v1 with these defaults:

- Admission policy: auto-write to `research/manual_inbox/`
- Source pack:
  - RSS/blog: quantocracy enabled, robotwealth and hudson-thames available but disabled by default
  - YouTube: add a small curated list only after you confirm channel choices
  - arXiv: enabled for `q-fin.TR`, `q-fin.PM`, `q-fin.ST`
- Noise policy: silent on zero, Telegram on non-zero
- Ranking: broad capture, defer hard judgment to later lifecycle stages
- Connectors:
  - required: `youtube-transcript-api` in repo venv
  - not required for v1: MCP, paid APIs, blogwatcher-cli

## Risks to flag before implementation

- YouTube transcript availability is uneven; some videos have no transcript or only auto-captions.
- Blog posts often describe ideas loosely; normalization may capture more prose than executable rules.
- Whitepapers can be too general for direct codegen; the capture stage should preserve provenance rather than over-claim actionability.
- Overly aggressive filtering risks missing good ideas; overly broad capture risks inbox spam.

## Execution order once approved

1. Implement schema + docs
2. Implement adapters + tests
3. Implement script + Makefile target
4. Install `youtube-transcript-api` in `.venv`
5. Dry-run locally
6. Add Hermes capture cron
7. Run one end-to-end verification into the research funnel
