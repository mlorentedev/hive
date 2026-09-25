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
3. **Worker tools** — `delegate_task` and `capture_lesson` each make one attempt against one OpenAI-compatible worker. No internal fallback: the caller owns the chain, and failures are classified as *pool unavailable* vs *task failed* so the caller can tell a retryable one from a real one.

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
| `src/hive/_compat.py` | Ghost-response counter surfaced by `vault_health` — see "MCP major and the cancel race" below |
| `src/hive/config.py` | `HiveSettings` (pydantic-settings, `HIVE_*` env vars) |
| `src/hive/budget.py` | SQLite budget tracker ($1/mo default cap, WAL mode) |
| `src/hive/clients.py` | Async HTTP client for any OpenAI-compatible `/v1` API (httpx) |
| `src/hive/relevance.py` | EMA-based section relevance scoring |
| `src/hive/frontmatter.py` | YAML frontmatter parse/validate/generate |
| `site/` | Astro + Starlight bilingual (EN/ES) docs site |

### MCP major and the cancel race

hive runs on **mcp 2.x / fastmcp 4.x on purpose** (#434): `pyproject.toml` has `mcp>=2.2,<3` and `fastmcp>=4,<5`, and `uv.lock` resolves mcp 2.2.0 / fastmcp 4.0.9.

- **Why the 1.x shim is gone.** On mcp 1.x, a response produced after its request was cancelled tripped `assert not self._completed` in `RequestResponder.respond`; the assertion reached the stdio receive loop's task group and killed the server, and every later call got `Connection closed` ([python-sdk#2416](https://github.com/modelcontextprotocol/python-sdk/issues/2416), still open for 1.x). hive monkey-patched that private method in `src/hive/_compat.py`. The 2.x dispatcher never answers a cancelled request, and the #2416 repro crashes 3/3 on 1.x and 0/3 on 2.x, so the patch was deleted rather than retargeted. The floors keep 1.x out, because nothing patches it any more.
- **What guards it now.** `tests/test_cancel_race.py` drives hive's own server with an uncancellable sync tool and fails if a cancelled call is answered twice or the server stops answering; it fails on 1.x without the patch. `tests/test_dependency_bounds.py` fails if either bound leaves its audited major, and if the suite runs on a version outside it. Dependabot's `semver-major` ignore rule is **not** a guard: it does not stop requirement-range widening, which got through twice (#316, #369).
- **CI installs from the lock.** `uv sync --locked --extra dev`, and `make install` runs `uv sync --extra dev`. The old `uv pip install -e .` resolved fresh from `pyproject.toml`, then the first `uv run` silently re-synced to `uv.lock`, so the install log showed mcp 2.2.0 while the tests ran on 1.28.1 (#434).
- **Still open.** `_compat.py` keeps only `GHOST_RESPONSES`, which `vault_health` reports as `ghost_responses` (ADR-007). Its `deadline` source still fires; nothing records its `cancellation` source on 2.x. Where the counter lives and what feeds that source is a separate, spec'd change.
- **2.x protocol notes.** A client on protocol revision 2026-07-28 opens with `server/discover` and never sends `initialize`, so anything keyed on `initialize` alone misses it (the daemon's `/status` session count did). `ToolAnnotations` takes snake_case fields (`read_only_hint`); the camelCase names are wire aliases.

### Worker routing order

`delegate_task` makes **one** attempt against **one** worker — a single OpenAI-compatible endpoint,
configured by `HIVE_WORKER_BASE_URL` / `HIVE_WORKER_API_KEY` / `HIVE_WORKER_MODEL`, each falling
back to its `HIVE_EMBED_*` counterpart when unset.

**Hive names no provider.** Any service or local runtime serving an OpenAI-compatible `/v1` is a
valid worker, and these three variables are the only way one is selected. A launcher that resolves
the credential under its own variable name maps it onto `HIVE_WORKER_API_KEY` at injection time —
hive does not accept provider-named aliases, because a published package reading one would be
reading a variable that means something in exactly one deployment (#391).

There is deliberately **no internal fallback chain**. Choosing among pools belongs to the caller
that owns a routing map; a backend that falls back on its own is a second routing authority, and its
answer can no longer be attributed to a model. Failures are classified rather than collapsed — *pool
unavailable* (unreachable, 429, auth) is distinct from *task failed* (the provider answered and the
answer is unusable), because a caller may retry the first elsewhere and must not retry the second.

Ollama and OpenRouter were removed in **4.0.0**; their model aliases (`auto`, `ollama`,
`openrouter-free`, `openrouter`) are rejected with a message naming the replacement rather than
silently ignored. There is no spend cap: the provider is a flat subscription, so the binding
constraint is concurrency, not cost.

## MCP tool schema rules (load-bearing)

These rules are not stylistic — violating them breaks the server in subtle, hard-to-diagnose ways.

- **NEVER use `| None` in MCP tool parameter types.** It generates `anyOf` in the JSON Schema and Claude Code silently drops the tool. Use empty-value defaults instead: `str = ""`, `list[T] = []` (with `# noqa: B006`), `int = 0`.
- **All `subprocess.run` calls MUST catch broad `Exception`**, not just `CalledProcessError` — git invocations on Windows raise things like `FileNotFoundError` that would otherwise crash a write tool.
- **All `httpx` calls MUST catch `httpx.TimeoutException`** (the umbrella class). `ConnectTimeout` alone misses `ReadTimeout`, which is what most slow-worker hangs surface as.
- Vault writes MUST validate YAML frontmatter (`hive.frontmatter`) and MUST attempt a git commit (best-effort, see `_helpers._git_commit`). Never fail a write because git failed.

## Commands

All routine commands go through the Makefile (uv-based):

```bash
make install    # uv sync --extra dev (installs exactly what uv.lock pins)
make lint       # ruff check src/ tests/
make typecheck  # mypy --strict src/
make test       # pytest with coverage (smoke tests auto-excluded via addopts)
make test-one   # run a single test: make test-one ARGS="tests/test_server.py -k vault_query"
make smoke      # pytest -m smoke (needs a reachable worker + HIVE_WORKER_API_KEY)
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

Smoke tests are marked `@pytest.mark.smoke` and excluded by default; they require a reachable worker endpoint (`HIVE_WORKER_BASE_URL`), its credential (`HIVE_WORKER_API_KEY`) and a model id the endpoint serves (`HIVE_WORKER_MODEL`). The skip condition **probes** the endpoint rather than checking that the variables are set — a configured-but-unreachable worker looking healthy is the defect #384 exists to fix.

## Configuration

`HiveSettings` (pydantic-settings) reads `HIVE_*` env vars. One setting accepts an unprefixed alias for ergonomic deploy: `VAULT_PATH` → `vault_path`. The `OPENROUTER_API_KEY` alias documented here previously went away with its setting in 4.0.0; no provider-named variable is read (#391). Vault default is `~/Projects/knowledge`. Worker DBs default to `~/.local/share/hive/{worker,relevance}.db`.

## Documentation site (i18n)

`site/` is bilingual Astro + Starlight (EN root locale, ES under `src/content/docs/es/`). **Rule:** any doc change must update both languages — edit English first, then mirror to `es/`. Sidebar labels with translations live in `site/astro.config.mjs`.

## PR workflow

- Branch from `master`, Conventional Commits (`feat:` / `fix:` / `docs:` / `chore:` …) — these drive release-please.
- `make check` must pass; CI runs on Python 3.12 and 3.13.
- Squash merge. Merging Conventional-Commit PRs to `master` triggers a release-please PR; merging that PR cuts a GitHub Release and publishes to PyPI (trusted publishing).
- Coding style: type hints everywhere (`mypy --strict`), Ruff formatting, functions < 40 lines, nesting < 4 levels.
- **Auto-merge is forbidden** (dotfiles AGENTS.md). Every PR merges deliberately after human review + green CI.
- **Before reporting PR work done, run `dotf pr triage-queue`** and record the dispositions on the PR under a `## Review triage` heading — including when there was nothing to dispose of, since an unwritten disposition is indistinguishable from nobody having looked. The reviewer registry it reads is [`harness/review-attestation.json`](harness/review-attestation.json); a non-zero exit is a queue to read, never an empty one, because the command exits non-zero both when work is pending and when it could not answer.
- **CodeRabbit's commit status reads `success` when it was rate limited and did not review** (measured on #405 and #406). A green check is not proof a review happened — tell a review from a notice by content: a review names files, lines or claims; a notice talks about the review itself. Merging unreviewed is allowed; saying nothing about it is not.
