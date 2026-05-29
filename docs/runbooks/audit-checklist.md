---
id: audit-checklist
type: runbook
status: active
created: "2026-03-03"
owner: manu
---

# Hive: Architecture Audit Checklist

> Run this checklist at major milestones or when onboarding.
> Last audit: 2026-03-09 (post-consolidation, 19→10 tools)

## 1. MCP Server (10 tools)

- [ ] `make check` passes (lint + mypy + tests)
- [ ] `uvx hive-vault` starts without errors
- [ ] `vault_list()` returns known projects
- [ ] `vault_list(project="hive")` returns files in project
- [ ] `vault_query(project="hive", section="context")` returns content
- [ ] `vault_search(query="active")` returns results across projects
- [ ] `vault_search(query="hive", ranked=True)` returns scored results
- [ ] `vault_search(since_days=7)` returns recent changes
- [ ] `vault_write(project, section, operation="append", content)` appends + commits
- [ ] `vault_write(project, path, content, doc_type, operation="create")` creates with frontmatter
- [ ] `vault_patch(project, path, old_text, new_text)` does surgical replace
- [ ] `vault_health()` reports file counts, stale files, missing sections
- [ ] `vault_health(checks=["frontmatter", "stale", "links"])` runs validation
- [ ] `vault_health(include_usage=True)` shows tool usage analytics
- [ ] `capture_lesson(project, title, context, problem, solution)` writes inline
- [ ] `session_briefing(project="hive")` returns tasks + lessons + git log + health
- [ ] `delegate_task(prompt="echo hello")` routes to Ollama or OpenRouter
- [ ] `delegate_task(project="hive", section="context")` summarizes vault file
- [ ] `worker_status()` returns budget, connectivity, and available models
- [ ] `list_resources()` returns 2 static + 3 templates
- [ ] `read_resource("hive://projects")` returns project listing

**Count:** 10 tools (7 vault + 1 session + 2 worker), 5 resources, 4 prompts

## 2. Configuration

- [ ] `HiveSettings` loads from environment with `HIVE_` prefix
- [ ] `OPENROUTER_API_KEY` bare alias works (backward compat)
- [ ] `VAULT_PATH` points to valid Obsidian vault
- [ ] `HIVE_OLLAMA_ENDPOINT` resolves to reachable Ollama instance
- [ ] `HIVE_DB_PATH` creates parent directories if missing
- [ ] `HIVE_LOG_PATH` creates rotating log file

## 3. Infrastructure

- [ ] Ollama (`ollama.kubelab.live:11434`) responds to health check
- [ ] OpenRouter API key is loaded (via age-encrypted dotfiles)
- [ ] MCP server registered in `~/.claude.json` (single hive server)
- [ ] Vault git repo (`~/Projects/knowledge/`) is clean, no uncommitted changes
- [ ] SQLite budget DB exists and is writable (WAL mode)

## 4. CI/CD

- [ ] GitHub Actions workflow runs on push to master
- [ ] CI matrix: Python 3.12 + 3.13
- [ ] Steps: ruff → mypy → pytest with coverage
- [ ] CI completes in <60s
- [ ] release-please configured for SemVer

## 5. Code Quality

- [ ] ruff check: zero warnings
- [ ] mypy --strict: zero errors
- [ ] Test count: ≥330
- [ ] Coverage: ≥90%
- [ ] No functions >40 lines
- [ ] No classes >250 lines
- [ ] No cyclomatic complexity >10

## 6. Security

- [ ] No hardcoded secrets in source
- [ ] API keys loaded from environment only
- [ ] OpenRouter key encrypted with age in dotfiles
- [ ] No SQL injection vectors (parameterized queries in budget.py)
- [ ] Frontmatter validation prevents arbitrary YAML injection on writes
- [ ] Git commits are atomic (add + commit in sequence)
- [ ] Regex patterns capped at 200 chars (ReDoS prevention)
- [ ] API error text truncated to 200 chars (info disclosure prevention)
- [ ] Path traversal blocked via resolve().relative_to()

## 7. Documentation

- [ ] `00-context.md` reflects current architecture and API surface
- [ ] ADR-001 (orchestration model) — accepted, still valid
- [ ] ADR-002 (system architecture) — accepted, matches shipped code
- [ ] `CLAUDE.md` in repo root has correct paths and commands
- [ ] `MEMORY.md` in auto-memory is current and concise
- [ ] README.md tool table matches actual tool count (10)
- [ ] Site docs match consolidated tool names
