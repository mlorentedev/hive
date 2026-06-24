# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Read [`AGENTS.md`](../AGENTS.md) first.** It is the cross-agent SSOT for this repo and carries the hive-specific content that used to live here: the MCP-server architecture + module table, the load-bearing MCP tool schema rules, the `_compat.py` cancellation shim, the worker routing order, the Makefile commands (and single-test fallback), configuration, the i18n docs-site rule, and the PR workflow. `AGENTS.md` in turn delegates the behavioural SSOT (Identity, Standing Orders, Decision Hierarchy, Model Selection, Neural Hive protocol, MCP usage rules, Spec-Driven Development gate) to the canonical dotfiles `AGENTS.md`.
>
> This file overlays only Claude Code-specific notes on top of `AGENTS.md`.

## Project knowledge

Project-bound knowledge lives in [`docs/`](../docs/) (docs-as-code): [`docs/adr/`](../docs/adr/) (architecture decisions + `sequence-diagrams.md`), [`docs/runbooks/`](../docs/runbooks/), [`docs/troubleshooting/`](../docs/troubleshooting/), [`docs/lessons.md`](../docs/lessons.md) (gotchas and post-mortems). The strategic/decide layer and session memory live in the maintainer's cross-project knowledge store (not committed here).

## Claude Code-specific notes

- **Model tier** (per `AGENTS.md` "Model Selection"): subagent frontmatter `model: opus|sonnet|haiku`; main session `/model` slash command. Top tier for hard debug / architecture / root-cause; Mid for mechanical refactor / docs / single-file fixes / test scaffolding; Low for syntax lookups / quick questions.
- **MEMORY.md / session memory** never live in this repo — they belong in the vault (GUARD-001). Use Hive as the memory API over the vault, not committed files here.
