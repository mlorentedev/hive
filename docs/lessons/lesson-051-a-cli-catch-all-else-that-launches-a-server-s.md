---
id: lesson-051-a-cli-catch-all-else-that-launches-a-server-s
type: lesson
status: active
created: "2026-06-04"
owner: manu
tags: [hive, lesson, cli, footgun, argv, version, PR-203]
---

# A CLI catch-all `else` that launches a server swallows `--version`/`--help`

**Context:** First Windows validation of the auto-update rollout (hive#176). Reflexively ran `hive --version` to read the installed version; instead it printed the FastMCP banner and blocked on the stdio MCP server — the command never returned. The version had to be read via `uv tool list` (which the rollout scripts already do, deliberately avoiding a `hive` probe).
**Problem:** `main()` dispatched with `if argv[0] == "serve" ... else: create_server().run()`. The `else` was a catch-all: ANY argv that wasn't `serve`/`service`/`client` — including `--version`, `--help`, and typos — fell through to launching the blocking stdio server. The load-bearing invariant is "bare `hive` (zero args) → server" (the v1 MCP per-session contract), but the code gated on "unrecognized argv → server", which is a strictly larger set. The two were conflated.
**Solution:** Gate the server launch on EMPTY argv, not unrecognized argv. Route explicit tokens: `-V`/`--version` → print version (exit 0), `-h`/`--help` → usage (exit 0), unknown token → usage error on stderr (exit 2). Bare invocation and serve/client/service routing unchanged, so no real consumer breaks (grep-verified across hive + dotfiles: every caller uses bare `hive-vault` / `hive client` / `hive serve` / `hive service`). A footgun on input nobody relied on → shipped as `fix:` (PR #203). Generalization: a default-action `else` in a CLI dispatcher is a footgun whenever the default has side effects (here: booting a daemon). Distinguish "no command given" (run the default) from "unknown command given" (exit-2 usage error).
**Tags:** `#cli` `#footgun` `#argv` `#version` `#PR-203`
