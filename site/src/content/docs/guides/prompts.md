---
title: Prompts
description: Built-in MCP prompts for structured workflows.
---

Hive includes 4 MCP prompts — structured protocols that any MCP client can invoke to follow multi-step workflows.

## retrospective

End-of-session review that extracts lessons and appends them to the vault.

**Parameters**: `project` (string)

**Protocol**:
1. Review work completed in the current session
2. Identify patterns, mistakes, and insights
3. Format as structured lessons
4. Use `vault_write` to append to the project's `90-lessons.md`

**Usage**: Ask your assistant to "run a retrospective for my-project" at the end of a work session.

## delegate

Structured protocol for delegating tasks to cheaper models via hive-worker.

**Parameters**: `task` (string)

**Protocol** (as the prompt states it today):
1. Suitability check against a delegatable / not-delegatable matrix
2. Budget check: `worker_status` for remaining budget and model availability
3. Context compression: summarize relevant vault files and strip the task to its essential instruction
4. Delegate: pick a tier with `worker_status`, then call `delegate_task`, one task per call
5. Evaluate the result, and handle the task directly if it is poor

The worker has no budget or model tiers any more; the prompt's wording catches up in [#520](https://github.com/mlorentedev/hive/issues/520), and this page changes with it.

**Usage**: Ask your assistant to "delegate this task: explain this regex"

## vault_sync

Post-sprint vault synchronization — reconcile documentation with shipped code.

**Parameters**: `project` (string)

**Protocol**:
1. Load current project context and tasks
2. Compare with recent git history
3. Identify stale docs, completed tasks, missing documentation
4. Update vault files to match current state

**Usage**: Ask your assistant to "sync the vault for my-project after this sprint"

## benchmark

Estimate token savings from hive MCP tools in the current session.

**Parameters**: none

**Protocol**:
1. Call `vault_health(include_usage=True)` to get tool call statistics
2. Estimate tokens that would have been consumed by static loading
3. Calculate savings percentage
4. Report results

**Usage**: Ask your assistant to "benchmark the token savings from hive"
