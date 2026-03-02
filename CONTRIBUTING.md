# Contributing

## Setup

```bash
git clone https://github.com/mlorentedev/hive.git
cd hive
make install
```

## Development

```bash
make check    # lint + typecheck + test (run before every PR)
make lint     # ruff only
make test     # pytest only
make build    # full build (runs check first)
```

## Pull Requests

1. Create a feature branch from `master`
2. Follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, etc.)
3. Run `make check` — all gates must pass
4. Open a PR against `master`
5. CI runs automatically (Python 3.12 + 3.13)
6. Squash merge after CI passes

## Code Standards

- Python 3.12+, type hints everywhere (`mypy --strict`)
- Formatting: Ruff
- Tests: pytest, TDD preferred (write test first)
- Functions < 40 lines, nesting < 4 levels

## Release Process

Automated via [release-please](https://github.com/googleapis/release-please). Merging to `master` with conventional commits triggers:

1. Release PR with changelog (auto-created)
2. Merge Release PR → GitHub Release + PyPI publish (trusted publishing)
