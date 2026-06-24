# AGENTS.md

> Instructions for AI coding agents (Claude Code, OpenCode, Copilot, Cursor, Codex, Antigravity) operating in this repo.
>
> **Behavioural SSOT lives in dotfiles:** `$DOTFILES_REPO_DIR/AGENTS.md` (the dotfiles repo root, resolved per-machine via the path cascade: explicit env → `~/.config/dotfiles/machine.json` → `env-contract.json` default; ADR-025). Read it FIRST — Identity, Standing Orders, Decision Hierarchy, Model Selection, Neural Hive protocol, MCP usage rules, Spec-Driven Development gate, and operational rules. This file adds ONLY what is specific to the Hive repo. The Claude-specific tooling overlay is `.claude/CLAUDE.md`.

## What this repo is

> **Hive** — vault-native AI orchestration: a unified MCP server giving AI coding assistants on-demand access to an Obsidian vault plus delegation to local/cheap workers.

Build/operate docs live in [`docs/`](docs/) (docs-as-code): [`docs/adr/`](docs/adr/) (architecture decisions + `sequence-diagrams.md`), [`docs/runbooks/`](docs/runbooks/), [`docs/troubleshooting/`](docs/troubleshooting/), [`docs/lessons.md`](docs/lessons.md). Per-feature specs live in `specs/`. Task state lives in the **bitácora** GitHub Project (filter by `Repo` = hive), not here. The cross-project brain and AI memory live in the maintainer's vault.

## Big-picture architecture

Hive is an MCP server (stdio transport, FastMCP framework) with three responsibilities:

1. **Vault tools** — query, search, list, write, patch markdown files in an Obsidian vault. All writes auto-commit to git (best-effort; git failure never crashes the server) and validate YAML frontmatter.
2. **Session tools** — `session_briefing` assembles tasks + lessons + git log + health in one call so an AI client gets ~50 lines of context instead of ~800.
3. **Worker tools** — `delegate_task` and `capture_lesson` route work down a cost ladder: Ollama (local, free) → OpenRouter free tier → OpenRouter paid (gated by $1/mo SQLite budget) → reject.

The package layout follows a deliberate split: `server.py` is a thin registration layer only. Each `_vault_*.py` / `_workers.py` module owns one tool family and registers its tools onto the FastMCP instance via a `register_*(mcp, ctx)` function. State lives in `ServerContext` (a dataclass in `_context.py`) and is passed to every handler — there is no module-level mutable state.

| Path | Role |
|---|---|
| `src/hive/server.py` | Thin registration layer — `create_server()`, resources, prompts |
| `src/hive/_context.py` | `ServerContext` dataclass — shared state for all handlers |
| `src/hive/_helpers.py` | Pure helpers — path resolution, formatting, git ops, tracking |
| `src/hive/_vault_read.py` | `vault_list`, `vault_query`, `vault_search`, `session_briefing` |
| `src/hive/_vault_write.py` | `vault_write`, `vault_patch` (both auto-commit to git) |
| `src/hive/_vault_health.py` | `vault_health` + health report builder |
| `src/hive/_workers.py` | `capture_lesson`, `delegate_task`, `worker_status` |
| `src/hive/_compat.py` | MCP cancellation shim — see "Compat shim" below |
| `src/hive/config.py` | `HiveSettings` (pydantic-settings, `HIVE_*` env vars) |
| `src/hive/budget.py` | SQLite budget tracker ($1/mo default cap, WAL mode) |
| `src/hive/clients.py` | Async HTTP clients (Ollama + OpenRouter, httpx) |
| `src/hive/relevance.py` | EMA-based section relevance scoring |
| `src/hive/frontmatter.py` | YAML frontmatter parse/validate/generate |
| `site/` | Astro + Starlight bilingual (EN/ES) docs site |

### Compat shim (do not delete blindly)

`src/hive/_compat.py` monkey-patches `mcp.shared.session.RequestResponder.__exit__` to swallow the spurious `CancelledError` that anyio re-raises after a cancelled tool call has already responded. Without it, a client sending `notifications/cancelled` kills the stdio receive loop and every subsequent call hangs (hive issue #75). The patch fires only on the exact failure mode and degrades silently if upstream removes the symbol. Delete only after confirming the upstream MCP fix has shipped.

**Upstream tracker:** [modelcontextprotocol/python-sdk#2610](https://github.com/modelcontextprotocol/python-sdk/issues/2610). `mcp` pinned `>=1.26,<2.0` in `pyproject.toml` so a major `RequestResponder` refactor cannot silently break the shim. **Escalation deadline:** 2026-06-12 — if upstream is still silent, port the fix upstream ([#127](https://github.com/mlorentedev/hive/issues/127)).

### Worker routing order

`delegate_task` tries clients in this order, falling through on failure or unavailability:

1. **Ollama** `qwen2.5-coder:7b` (local) — free, primary
2. **OpenRouter** `qwen/qwen3-coder:free` — free tier fallback
3. **OpenRouter** paid (`qwen/qwen3-coder`) — only if caller passes `max_cost_per_request > 0` AND `BudgetTracker` allows it
4. **Reject** — surface the error to the client; Claude handles fallback

## MCP tool schema rules (load-bearing)

These rules are not stylistic — violating them breaks the server in subtle, hard-to-diagnose ways.

- **NEVER use `| None` in MCP tool parameter types.** It generates `anyOf` in the JSON Schema and Claude Code silently drops the tool. Use empty-value defaults instead: `str = ""`, `list[T] = []` (with `# noqa: B006`), `int = 0`.
- **All `subprocess.run` calls MUST catch broad `Exception`**, not just `CalledProcessError` — git invocations on Windows raise things like `FileNotFoundError` that would otherwise crash a write tool.
- **All `httpx` calls MUST catch `httpx.TimeoutException`** (the umbrella class). `ConnectTimeout` alone misses `ReadTimeout`, which is what most slow-worker hangs surface as.
- Vault writes MUST validate YAML frontmatter (`hive.frontmatter`) and MUST attempt a git commit (best-effort, see `_helpers._git_commit`). Never fail a write because git failed.

## Commands

All routine commands go through the Makefile (uv-based):

```bash
make install    # uv venv + uv pip install -e ".[dev]"
make lint       # ruff check src/ tests/
make typecheck  # mypy --strict src/
make test       # pytest with coverage (smoke tests auto-excluded via addopts)
make test-one   # run a single test: make test-one ARGS="tests/test_server.py -k vault_query"
make smoke      # pytest -m smoke (needs running Ollama + OPENROUTER_API_KEY)
make check      # lint + typecheck + test — run this before every PR
make build      # check + uv build
make run        # uv run python -m hive.server (local MCP server over stdio)
make logs       # show path to the debug log file (also printed at server startup)
make clean      # remove build/cache artifacts (cross-platform via Python)
make site / site-dev / site-preview  # Astro docs site
```

### Running a single test

The Makefile does not expose this — fall back to `uv run pytest` directly:

```bash
uv run pytest tests/test_server.py                                # one file
uv run pytest tests/test_server.py::test_vault_query_returns_file # one test
uv run pytest tests/test_server.py -k "vault_query"               # by keyword
uv run pytest -m smoke -k worker_status                           # smoke subset
```

Smoke tests are marked `@pytest.mark.smoke` and excluded by default (`addopts = "-m 'not smoke'"` in `pyproject.toml`); they require a live Ollama at `OLLAMA_ENDPOINT` and an `OPENROUTER_API_KEY`.

## Configuration

`HiveSettings` (pydantic-settings) reads `HIVE_*` env vars. Two settings accept an unprefixed alias for ergonomic deploy: `VAULT_PATH` → `vault_path`, `OPENROUTER_API_KEY` → `openrouter_api_key`. Vault default is `~/Projects/knowledge`. Worker DBs default to `~/.local/share/hive/{worker,relevance}.db`.

## Documentation site (i18n)

`site/` is bilingual Astro + Starlight (EN root locale, ES under `src/content/docs/es/`). **Rule:** any doc change must update both languages — edit English first, then mirror to `es/`. Sidebar labels with translations live in `site/astro.config.mjs`.

## PR workflow

- Branch from `master`, Conventional Commits (`feat:` / `fix:` / `docs:` / `chore:` …) — these drive release-please.
- `make check` must pass; CI runs on Python 3.12 and 3.13.
- Squash merge. Merging Conventional-Commit PRs to `master` triggers a release-please PR; merging that PR cuts a GitHub Release and publishes to PyPI (trusted publishing).
- Coding style: type hints everywhere (`mypy --strict`), Ruff formatting, functions < 40 lines, nesting < 4 levels.
- **Auto-merge is forbidden** (dotfiles AGENTS.md). Every PR merges deliberately after human review + green CI.
