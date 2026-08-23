.PHONY: help install lint typecheck test test-one smoke check build clean run logs site site-dev site-preview
.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install deps
	uv venv && uv pip install -e ".[dev]"

lint: ## Run ruff linter + formatter check
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck: ## Run mypy --strict
	uv run mypy src/

test: ## Run unit + integration tests
	uv run pytest tests/ -v --cov=hive --cov-report=term-missing

test-one: ## Run a single test file or expression (e.g. `make test-one ARGS="tests/test_server.py -k vault_query"`)
	uv run pytest $(ARGS)

smoke: ## Run e2e smoke tests (needs a reachable worker + HIVE_WORKER_API_KEY)
	uv run pytest -m smoke -v

check: lint typecheck test ## Lint + typecheck + test

build: check ## Check + build package
	uv build

run: ## Run Hive MCP server locally
	uv run python -m hive.server

logs: ## Show path to the debug log file
	@echo "$${HIVE_LOG_PATH:-$$HOME/.local/share/hive/hive.log}"
	@echo
	@echo "Tail with: tail -f \"$${HIVE_LOG_PATH:-$$HOME/.local/share/hive/hive.log}\""

site: ## Build landing page (requires Node.js)
	cd site && npm ci && npm run build

site-dev: ## Start landing page dev server
	cd site && npm run dev

site-preview: ## Preview landing page production build
	cd site && npm run preview

clean: ## Remove build artifacts (cross-platform)
	uv run python -c "import shutil, pathlib; \
	  dirs = ['dist', '.venv', '.ruff_cache', '.mypy_cache', \
	          '.pytest_cache', 'htmlcov', 'site/dist', 'site/node_modules']; \
	  [shutil.rmtree(d, ignore_errors=True) for d in dirs if pathlib.Path(d).exists()]; \
	  [pathlib.Path(f).unlink(missing_ok=True) for f in ['.coverage', '*.egg-info']]"
