---
title: Obsidian-Git Integration
description: Cooperate with the obsidian-git Obsidian plugin to avoid lock contention and amortize commits.
---

If your vault is also opened in Obsidian with the [obsidian-git](https://github.com/Vinzent03/obsidian-git) plugin auto-committing every N minutes, hive's per-write `git commit` and obsidian-git's interval commit can race for `.git/index.lock`. From a hive operator's perspective this looks like silent 30-second freezes coinciding with obsidian-git's auto-save tick.

HIVE-115 PR-4 introduces an **opt-in cooperation mode**: when enabled, hive detects obsidian-git's health and lets it handle the commit, while still writing files to disk synchronously. Falls back to hive's own commit when obsidian-git is broken or absent.

## Enabling the feature

Set the environment variable when registering hive with your MCP client:

```bash
# Claude Code
claude mcp add -s user hive \
  -e VAULT_PATH=$HOME/your-vault \
  -e HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true \
  -- uvx --upgrade hive-vault
```

Default is `false`. Setting any of `true`, `yes`, `1`, `on` (case-insensitive) opts in; everything else (including unset) keeps the pre-PR-4 behaviour of committing inline.

## When hive defers

The composite predicate (per ADR-010 + the 2026-05-22 pre-PR-3 audit M4):

```
defer ⇔
    env "HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER" == "true"
    AND obsidian-git plugin is installed (data.json present, commitInterval > 0)
    AND (
        (last commit age < 2 * autoSaveInterval)
        OR
        ( git status --porcelain produces empty output )   # idle vault
    )
```

- **Recent commit window** is `2 × commitInterval` (minutes from obsidian-git's own settings). With the default 10-minute auto-save, hive considers commits within the last 20 minutes "recent".
- **Idle vault** is treated as deferable. If `git status --porcelain` is empty, there's nothing to commit anyway — the external committer hasn't stalled, it has nothing to do. Safe.
- **Dirty + stale** is the only case that falls back. obsidian-git is configured but neither committing on schedule nor leaving the vault clean ⇒ probably paused / broken ⇒ hive commits the write itself.

## Response surface

| Scenario | `vault_write` / `vault_patch` response suffix |
|----------|----------------------------------------------|
| `commit=True`, no defer | `""` (committed inline by hive — unchanged from pre-PR-4) |
| `commit=True`, deferred | `" (deferred to obsidian-git; will be picked up on its next tick)"` |
| default (deferred) | `" (queued — commits on the next reconciler tick)"` |
| default, with no reconciler running | `" (uncommitted — call vault_commit to flush)"` |

The deferred suffix makes the behaviour change visible to operators inspecting tool logs — no silent semantics flip.

## Health probe internals

The probe issues two cheap git invocations:

- `git log -1 --format=%ct` — Unix timestamp of HEAD's commit.
- `git status --porcelain` — empty when the working tree matches HEAD.

Both run via `subprocess.run` (read paths, advisory `tool_timeout`; no `bounded_call` termination authority needed). Each is bounded to 10 seconds by `subprocess.run(timeout=10)`. Any non-zero return code or exception is treated as "unhealthy ⇒ do not defer".

## Reinforcement outbox (related)

Lesson reinforcement counters route through a per-process in-memory **outbox** drained by a daemon reconciler thread every `HIVE_OUTBOX_TICK_S` (default 5 seconds). This amortizes ~5 SQLite write transactions per second down to one batched UPSERT per tick, while keeping `same-process read after write` immediate (read paths flush the outbox synchronously before querying).

Cross-process consistency is eventual within `~2 × HIVE_OUTBOX_TICK_S`. Acceptable for ranking signals; **never use this pattern for durable state**. The outbox's docstring spells the crash-loss contract explicitly.

## Tuning recommendations

| Workload | `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER` | `HIVE_OUTBOX_TICK_S` | Notes |
|----------|------------------------------------------|----------------------|-------|
| Solo developer, Obsidian open | `true` | `5.0` (default) | Eliminates the 30s freeze pattern under obsidian-git contention. |
| CI / headless agent (no Obsidian) | `false` (default) | `5.0` | obsidian-git not present → defer predicate returns False anyway. Explicit `false` documents intent. |
| Agent fleet, no Obsidian, latency-sensitive | `false` | `2.0` | Faster cross-process visibility for reinforcement counters at the cost of slightly higher SQLite write load. |
| Air-gapped, file-only sync (Syncthing / rsync) | `false` | `30.0` | No external committer to cooperate with; less frequent reconciler ticks reduce wakeups. |

## Troubleshooting

**Symptom:** `vault_write` always returns the `"(deferred ...)" ` suffix even after closing Obsidian.

- obsidian-git's `data.json` persists in `<vault>/.obsidian/plugins/obsidian-git/data.json` even when Obsidian is closed. The probe checks the file, not whether Obsidian is running. Disable the plugin (or remove the directory) to fall back to hive committing.

**Symptom:** Deferred writes never show up in `git log`.

- The plugin's `autoSaveInterval` must be > 0. Inspect `data.json`: `commitInterval` field. If `0`, the plugin is not auto-committing and hive's defer predicate will return False (the `commit_interval > 0` clause in `detect_obsidian_git`).
- In Obsidian, open the obsidian-git settings panel and verify "Backup interval" is > 0 minutes.

**Symptom:** hive commits inline despite `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true`.

- The probe found one of: env not parsed as truthy (typo?), obsidian-git not installed in this vault, or `recent_commit ∨ empty_porcelain` was False (vault dirty + last obsidian-git commit > `2 * interval` ago). The inline commit IS the safety fallback.

## See also

- [Configuration env vars](/configuration/) — full `HIVE_*` reference.
- [Troubleshooting — Multi-session contention](/guides/troubleshooting/#multi-session-contention) — sibling techniques for N=3-5 hive processes against the same vault.
- ADR-010 (in the maintainer's vault) — design rationale for the cooperate-don't-compete pattern.
