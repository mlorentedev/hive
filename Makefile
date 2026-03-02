.PHONY: install lint typecheck test check build clean

install:
	uv venv && uv pip install -e ".[dev]"

lint:
	uv run ruff check src/ tests/

typecheck:
	uv run mypy src/

test:
	uv run pytest tests/ -v --cov=hive --cov-report=term-missing

check: lint typecheck test

build: check
	uv build

clean:
	rm -rf dist/ .venv/ *.egg-info/ .ruff_cache/ .mypy_cache/ .pytest_cache/ htmlcov/ .coverage
