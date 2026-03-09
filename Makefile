# =============================================================================
# Nautilus-Predict — Task Runner
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

PYTHON := python3
PIP := $(PYTHON) -m pip
RUFF := $(PYTHON) -m ruff
PYTEST := $(PYTHON) -m pytest
MYPY := $(PYTHON) -m mypy
PRE_COMMIT := $(PYTHON) -m pre_commit
SRC := src/nautilus_predict
TEST_DIR := tests
SCRIPTS := scripts

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help:
	@echo ""
	@echo "Nautilus-Predict — Available Commands"
	@echo "======================================"
	@echo ""
	@echo "Setup:"
	@echo "  make dev          Install all dependencies (including dev tools)"
	@echo "  make install      Install production dependencies only"
	@echo "  make rust-build   Build polyfill-rs Rust crate"
	@echo ""
	@echo "Quality:"
	@echo "  make lint         Run ruff linter"
	@echo "  make format       Auto-format with ruff"
	@echo "  make type-check   Run mypy type checker"
	@echo "  make check        lint + type-check (no auto-fix)"
	@echo "  make test         Run full test suite with verbose output"
	@echo ""
	@echo "Trading:"
	@echo "  make paper        Start paper trading (live feeds, simulated fills)"
	@echo "  make backtest     Run backtesting session on stored Parquet data"
	@echo "  make live         Start LIVE trading (requires LIVE_TRADING_CONFIRMED=true)"
	@echo ""
	@echo "Operations:"
	@echo "  make check-env    Validate environment variables and API connectivity"
	@echo "  make fetch-markets  List active Polymarket markets"
	@echo "  make check-pos    Show current positions on both venues"
	@echo "  make derive-keys  Derive Polymarket L2 API credentials (one-time)"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build Build Docker image using the NautilusTrader base"
	@echo "  make docker-up    Start Docker container (paper mode)"
	@echo "  make docker-down  Stop Docker container"
	@echo "  make docker-logs  Tail container logs"
	@echo "  make docker-shell Open a shell inside the running container"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean        Remove build artifacts and caches"
	@echo "  make pre-commit-install  Install pre-commit hooks"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: dev
dev: ## Install all dependencies including dev tools
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "Installation complete. Next steps:"
	@echo "  1. cp .env.example .env && edit .env with your credentials"
	@echo "  2. make check-env"
	@echo "  3. make paper"

.PHONY: install
install: ## Install production dependencies only
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
	TRADING_MODE=paper $(PYTHON) -m nautilus_predict.main --mode paper

.PHONY: backtest
backtest: ## Run backtesting session on stored Parquet data
	@echo "Starting backtest session..."
	TRADING_MODE=backtest $(PYTHON) -m nautilus_predict.main --mode backtest

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
	$(PYTHON) -m nautilus_predict.main --mode live

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
download-data: ## Download Polymarket historical data (set TOKEN_ID env var)
	@if [ -z "$(TOKEN_ID)" ]; then \
		echo "Usage: make download-data TOKEN_ID=0x<hex>"; \
		exit 1; \
	fi
	$(PYTHON) $(SCRIPTS)/download_polymarket_data.py --token-id $(TOKEN_ID)

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
