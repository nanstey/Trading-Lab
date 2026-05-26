# =============================================================================
# Trading Lab — Task Runner
# =============================================================================
# Prerequisites: Python 3.12+, Rust/cargo (for polyfill-rs)
# Docker: NautilusTrader container for paper/live execution
#
# Quick start:
#   make dev       — install all dependencies in your environment
#   make test      — run full test suite
#   make paper     — start paper trading (safe, no real orders)
#   make docker-up — run paper trading inside the NautilusTrader container
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV    := .venv
PYTHON  := $(VENV)/bin/python3
PIP     := $(PYTHON) -m pip
RUFF    := $(PYTHON) -m ruff
PYTEST  := $(PYTHON) -m pytest
MYPY    := $(PYTHON) -m mypy
PRE_COMMIT := $(PYTHON) -m pre_commit
SRC := src/trading_lab
TEST_DIR := tests
SCRIPTS := scripts

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help:
	@echo ""
	@echo "Trading Lab — Available Commands"
	@echo "======================================"
	@echo "Fresh-machine setup: see README.md 'Fresh-machine setup' section"
	@echo ""
	@echo "Setup:"
	@echo "  make dev              Create .venv via uv + install deps (incl. dev tools)"
	@echo "  make install          Production deps only"
	@echo "  make rust-build       Build polyfill-rs Rust crate (optional)"
	@echo ""
	@echo "Quality:"
	@echo "  make lint             ruff check"
	@echo "  make format           ruff format (auto-fix)"
	@echo "  make type-check       mypy"
	@echo "  make check            lint + type-check (no auto-fix)"
	@echo "  make test             pytest with coverage"
	@echo ""
	@echo "Backtest / Strategy dev:"
	@echo "  make backtest                                      Single-strategy backtest"
	@echo "  make research-capture [SOURCE_ARGS='--all --dry-run']   Poll external sources into manual_inbox"
	@echo "  make research-link-dropbox [SOURCE_ARGS='--dry-run']      Process dropped YouTube links"
	@echo "  make drop-youtube-link URL='https://youtu.be/...'         Add one YouTube URL to dropbox"
	@echo "  make research-discover [RSS=1]                     Drain manual_inbox (+ RSS)"
	@echo "  make research-test SLUG=<slug> START=YYYY-MM-DD END=YYYY-MM-DD"
	@echo "  make research-optimize SLUG=<slug> START=... END=... [WORKERS=4]"
	@echo "  make research-status [SLUG=<slug>]                 Lifecycle state inspector"
	@echo "  make research-validate                             5.11 known-bad + known-good"
	@echo ""
	@echo "Paper trading (real NT TradingNode, is_paper=True):"
	@echo "  make paper-run SLUG=<slug> [DURATION_SECS=600]     PaperRunnerV2 (recommended)"
	@echo "  make paper-run-legacy SLUG=<slug>                  GenericPaperRunner (legacy)"
	@echo "  make paper-summary SLUG=<slug> [DATE=YYYYMMDD]     Realised-PnL report"
	@echo "  make paper-watcher                                 Auto-retirement rules"
	@echo ""
	@echo "Live trading (REAL ORDERS, triple opt-in required):"
	@echo "  make live-run SLUG=<slug> [CONFIRM=1]              Pre-flight only without CONFIRM"
	@echo ""
	@echo "Data:"
	@echo "  make sync-markets                                  Gamma → market_catalog.db"
	@echo "  make sync-markets-full                             Including closed/archived"
	@echo "  make download-data CONDITION_ID=0x... [START=...] [END=...]"
	@echo "  make data-ingest [SLUGS=a,b] [DURATION_SECS=...]   Continuous WS daemon"
	@echo "  make rolling-eval [WINDOW_DAYS=2] [STATES=PAPER,OPTIMIZE]"
	@echo ""
	@echo "Operator harness:"
	@echo "  make operator-brief [MD=1]                         JSON (or --md) for SMS agent"
	@echo "  make portfolio-status [MD=1]                       Per-slug capital caps + headroom"
	@echo ""
	@echo "Environment + creds:"
	@echo "  make check-env                                     Validate .env + connectivity"
	@echo "  make fetch-markets                                 List active PM markets"
	@echo "  make check-pos                                     Open positions across venues"
	@echo "  make derive-keys                                   One-time L2 cred derivation"
	@echo ""
	@echo "Docker (optional):"
	@echo "  make docker-build / docker-up / docker-down / docker-logs / docker-shell"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean / clean-rust / pre-commit-install / dirs"
	@echo ""
	@echo "Risk control (any time):"
	@echo "  .venv/bin/python scripts/halt_trading.py --reason \"...\"   Trip kill switch"
	@echo "  .venv/bin/python scripts/reset_kill_switch.py --confirm   Clear it"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: venv
venv: ## Create .venv (skips if already exists)
	@test -d $(VENV) || python3 -m venv $(VENV)

.PHONY: dev
dev: venv ## Install all dependencies including dev tools
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "Installation complete. Next steps:"
	@echo "  1. cp .env.example .env && edit .env with your credentials"
	@echo "  2. make check-env"
	@echo "  3. make paper"

.PHONY: install
install: venv ## Install production dependencies only
	$(PIP) install -e "."

.PHONY: rust-build
rust-build: ## Build polyfill-rs Rust crate
	@echo "Building polyfill-rs..."
	cd polyfill-rs && cargo build --release
	@echo "Rust build complete."

.PHONY: rust-test
rust-test: ## Run polyfill-rs Rust tests
	cd polyfill-rs && cargo test

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

.PHONY: lint
lint: ## Run ruff linter (no auto-fix)
	$(RUFF) check $(SRC) $(TEST_DIR) $(SCRIPTS)

.PHONY: lint-fix
lint-fix: ## Run ruff linter with auto-fix
	$(RUFF) check --fix $(SRC) $(TEST_DIR) $(SCRIPTS)

.PHONY: format
format: ## Auto-format with ruff
	$(RUFF) format $(SRC) $(TEST_DIR) $(SCRIPTS)

.PHONY: format-check
format-check: ## Check formatting without modifying files
	$(RUFF) format --check $(SRC) $(TEST_DIR) $(SCRIPTS)

.PHONY: type-check
type-check: ## Run mypy type checker
	$(MYPY) $(SRC)

.PHONY: check
check: lint format-check type-check ## Run all checks (no auto-fix)

.PHONY: pre-commit-install
pre-commit-install: ## Install pre-commit hooks into .git/hooks
	$(PRE_COMMIT) install

.PHONY: pre-commit-run
pre-commit-run: ## Run pre-commit hooks on all files
	$(PRE_COMMIT) run --all-files

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

.PHONY: test
test: ## Run full test suite with coverage report
	$(PYTEST) $(TEST_DIR) -v

.PHONY: test-fast
test-fast: ## Run tests, skip slow/integration tests
	$(PYTEST) $(TEST_DIR) -v -m "not integration and not slow"

.PHONY: test-cov
test-cov: ## Run tests and open HTML coverage report
	$(PYTEST) $(TEST_DIR) --cov=$(SRC) --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

# ---------------------------------------------------------------------------
# Trading Modes
# ---------------------------------------------------------------------------

.PHONY: paper
paper: ## Start paper trading (safe — live feeds, no real orders)
	@echo "Starting paper trading mode..."
	@echo "Note: Uses live market data feeds. No real orders placed."
	TRADING_MODE=paper $(PYTHON) -m trading_lab.main --mode paper

.PHONY: backtest
backtest: ## Run backtesting session on stored Parquet data
	@echo "Starting backtest session..."
	TRADING_MODE=backtest $(PYTHON) -m trading_lab.main --mode backtest

.PHONY: live
live: ## Start LIVE trading — REAL MONEY, requires double opt-in
	@echo ""
	@echo "=================================================================="
	@echo "  WARNING: LIVE TRADING MODE — REAL FUNDS AT RISK"
	@echo "=================================================================="
	@echo ""
	@echo "This will execute REAL orders with REAL money on Polymarket."
	@echo ""
	@echo "Required:"
	@echo "  export TRADING_MODE=live"
	@echo "  export LIVE_TRADING_CONFIRMED=true"
	@echo ""
	@if [ "$(TRADING_MODE)" != "live" ] || [ "$(LIVE_TRADING_CONFIRMED)" != "true" ]; then \
		echo "ABORTED: Set TRADING_MODE=live and LIVE_TRADING_CONFIRMED=true to proceed."; \
		exit 1; \
	fi
	@echo "Live trading confirmed. Starting..."
	$(PYTHON) -m trading_lab.main --mode live

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

.PHONY: check-env
check-env: ## Validate environment variables and API connectivity
	$(PYTHON) $(SCRIPTS)/check_env.py --verbose

.PHONY: check-env-offline
check-env-offline: ## Validate environment variables (skip connectivity)
	$(PYTHON) $(SCRIPTS)/check_env.py --no-connectivity --verbose

.PHONY: fetch-markets
fetch-markets: ## List active Polymarket markets
	$(PYTHON) $(SCRIPTS)/fetch_markets.py

.PHONY: check-pos
check-pos: ## Show current open positions on both venues
	$(PYTHON) $(SCRIPTS)/check_positions.py

.PHONY: derive-keys
derive-keys: ## Derive Polymarket L2 API credentials (one-time setup)
	@echo "NOTE: This will make an authenticated request to Polymarket."
	@echo "Ensure POLY_PRIVATE_KEY is set in your .env file."
	$(PYTHON) $(SCRIPTS)/derive_polymarket_keys.py

.PHONY: download-data
download-data: ## Download Polymarket historical trades for CONDITION_ID
	@if [ -z "$(CONDITION_ID)" ]; then \
		echo "Usage: make download-data CONDITION_ID=0x<hex> [START=YYYY-MM-DD] [END=YYYY-MM-DD]"; \
		exit 1; \
	fi
	$(PYTHON) $(SCRIPTS)/download_polymarket_data.py --condition-id $(CONDITION_ID) \
	    $(if $(START),--start $(START),) $(if $(END),--end $(END),)

.PHONY: sync-markets
sync-markets: ## Refresh active markets metadata (gamma → sqlite catalog)
	$(PYTHON) $(SCRIPTS)/sync_market_metadata.py --active-only

.PHONY: sync-markets-full
sync-markets-full: ## Sync ALL markets including closed/archived
	$(PYTHON) $(SCRIPTS)/sync_market_metadata.py --full

# ---------------------------------------------------------------------------
# Research / agentic loop
# ---------------------------------------------------------------------------

.PHONY: research-capture
research-capture: ## Poll external strategy sources into manual_inbox
	$(PYTHON) $(SCRIPTS)/capture_strategy_ideas.py $(SOURCE_ARGS)

.PHONY: research-link-dropbox
research-link-dropbox: ## Process dropped YouTube links into manual_inbox
	$(PYTHON) $(SCRIPTS)/process_link_dropbox.py $(SOURCE_ARGS)

.PHONY: drop-youtube-link
drop-youtube-link: ## Add one YouTube URL to research/link_dropbox
	@if [ -z "$(URL)" ]; then echo "Usage: make drop-youtube-link URL=https://youtu.be/<id>"; exit 1; fi
	$(PYTHON) $(SCRIPTS)/drop_youtube_link.py "$(URL)"

.PHONY: research-discover
research-discover: ## Drain manual_inbox + (optional) RSS feeds → PROPOSED
	$(PYTHON) $(SCRIPTS)/discover_strategies.py $(if $(RSS),--rss,)

.PHONY: research-test
research-test: ## Evaluate one SLUG (eval_strategy.py)
	@if [ -z "$(SLUG)" ]; then echo "Usage: make research-test SLUG=<slug> START=YYYY-MM-DD END=YYYY-MM-DD"; exit 1; fi
	$(PYTHON) $(SCRIPTS)/eval_strategy.py --slug $(SLUG) --start $(START) --end $(END)

.PHONY: research-optimize
research-optimize: ## Walk-forward optimise one SLUG
	@if [ -z "$(SLUG)" ]; then echo "Usage: make research-optimize SLUG=<slug> START=YYYY-MM-DD END=YYYY-MM-DD"; exit 1; fi
	$(PYTHON) $(SCRIPTS)/optimize_strategy.py --slug $(SLUG) --data-start $(START) --data-end $(END)

.PHONY: research-status
research-status: ## Inspect a hypothesis (state + history + last experiment)
	@if [ -z "$(SLUG)" ]; then $(PYTHON) $(SCRIPTS)/research_cli.py list; else $(PYTHON) $(SCRIPTS)/research_cli.py show --slug $(SLUG); fi

.PHONY: research-validate
research-validate: ## Phase 5.11 — drive known-bad + known-good through the loop
	$(PYTHON) $(SCRIPTS)/validate_loop.py

.PHONY: paper-summary
paper-summary: ## Realised-PnL report for a PAPER slug (today's log)
	@if [ -z "$(SLUG)" ]; then echo "Usage: make paper-summary SLUG=<slug> [DATE=YYYYMMDD]"; exit 1; fi
	$(PYTHON) $(SCRIPTS)/paper_summary.py --slug $(SLUG) $(if $(DATE),--date $(DATE),)

.PHONY: paper-watcher
paper-watcher: ## Run the auto-retirement watcher across PAPER strategies
	$(PYTHON) $(SCRIPTS)/paper_watcher.py

.PHONY: paper-run
paper-run: ## Drive PaperRunnerV2 (real TradingNode, is_paper=True) on a PAPER slug
	@if [ -z "$(SLUG)" ]; then echo "Usage: make paper-run SLUG=<slug> [DURATION_SECS=600]"; exit 1; fi
	$(PYTHON) $(SCRIPTS)/paper_run_v2.py --slug $(SLUG) $(if $(DURATION_SECS),--duration-secs $(DURATION_SECS),)

.PHONY: paper-run-legacy
paper-run-legacy: ## Legacy GenericPaperRunner (monkey-patched, no real exec path)
	@if [ -z "$(SLUG)" ]; then echo "Usage: make paper-run-legacy SLUG=<slug> [DURATION_SECS=600]"; exit 1; fi
	$(PYTHON) $(SCRIPTS)/paper_run.py --slug $(SLUG) $(if $(DURATION_SECS),--duration-secs $(DURATION_SECS),)

.PHONY: live-run
live-run: ## LIVE — same as paper-run but with REAL ORDERS. Requires TRADING_MODE=live + LIVE_TRADING_CONFIRMED=true
	@if [ -z "$(SLUG)" ]; then echo "Usage: make live-run SLUG=<slug> [DURATION_SECS=3600] [CONFIRM=1]"; exit 1; fi
	$(PYTHON) $(SCRIPTS)/live_run.py --slug $(SLUG) $(if $(DURATION_SECS),--duration-secs $(DURATION_SECS),) $(if $(CONFIRM),--i-understand-this-is-live,)

.PHONY: data-ingest
data-ingest: ## Continuous data ingestion daemon (long-lived; SIGINT to stop)
	$(PYTHON) $(SCRIPTS)/run_ingestion.py $(if $(SLUGS),--slugs $(SLUGS),) $(if $(DURATION_SECS),--duration-secs $(DURATION_SECS),)

.PHONY: rolling-eval
rolling-eval: ## Re-evaluate each PAPER strategy on the last WINDOW_DAYS of data (default 2)
	$(PYTHON) $(SCRIPTS)/rolling_eval.py $(if $(WINDOW_DAYS),--window-days $(WINDOW_DAYS),) $(if $(STATES),--states $(STATES),)

.PHONY: operator-brief
operator-brief: ## Operator briefing (JSON for the remote agent; --md for human-readable)
	$(PYTHON) $(SCRIPTS)/operator_briefing.py $(if $(MD),--md,)

.PHONY: portfolio-status
portfolio-status: ## Show per-slug capital caps. MD=1 for markdown; REFRESH=1 to fetch live PM equity
	$(PYTHON) $(SCRIPTS)/portfolio_status.py $(if $(MD),--md,) $(if $(REFRESH),--refresh,)

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

.PHONY: docker-build
docker-build: ## Build Docker image
	docker compose build

.PHONY: docker-up
docker-up: ## Start Docker container in paper mode
	docker compose up -d
	@echo "Container started. Tail logs with: make docker-logs"

.PHONY: docker-down
docker-down: ## Stop and remove Docker container
	docker compose down

.PHONY: docker-logs
docker-logs: ## Tail Docker container logs
	docker compose logs -f trader

.PHONY: docker-shell
docker-shell: ## Open a shell in the running container
	docker compose exec trader bash

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build artifacts, caches, and test output
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	@echo "Clean complete."

.PHONY: clean-rust
clean-rust: ## Clean Rust build artifacts
	cd polyfill-rs && cargo clean

.PHONY: dirs
dirs: ## Create runtime directories (data, logs, catalog)
	mkdir -p data/parquet data/raw logs catalog
	@echo "Runtime directories created."
