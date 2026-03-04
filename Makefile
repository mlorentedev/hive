.PHONY: help install lint typecheck test smoke check build clean run
.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install deps
	uv venv && uv pip install -e ".[dev]"

lint: ## Run ruff linter
	uv run ruff check src/ tests/

typecheck: ## Run mypy --strict
	uv run mypy src/

test: ## Run unit + integration tests
	uv run pytest tests/ -v --cov=hive --cov-report=term-missing

smoke: ## Run e2e smoke tests (needs Ollama + API key)
	uv run pytest -m smoke -v

check: lint typecheck test ## Lint + typecheck + test

build: check ## Check + build package
	uv build

run: ## Run Hive MCP server locally
	uv run python -m hive.server

clean: ## Remove build artifacts
	rm -rf dist/ .venv/ *.egg-info/ .ruff_cache/ .mypy_cache/ .pytest_cache/ htmlcov/ .coverage
