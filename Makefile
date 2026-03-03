.PHONY: install lint typecheck test smoke check build clean run-vault run-worker

install:
	uv venv && uv pip install -e ".[dev]"

lint:
	uv run ruff check src/ tests/

typecheck:
	uv run mypy src/

test:
	uv run pytest tests/ -v --cov=hive --cov-report=term-missing

smoke:
	uv run pytest -m smoke -v

check: lint typecheck test

build: check
	uv build

run-vault:
	uv run python -m hive.vault_server

run-worker:
	uv run python -m hive.worker_server

clean:
	rm -rf dist/ .venv/ *.egg-info/ .ruff_cache/ .mypy_cache/ .pytest_cache/ htmlcov/ .coverage
