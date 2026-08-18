---
id: lesson-030-forward-compatible-monkey-patches-must-be-sel
type: lesson
status: active
created: "2026-05-15"
owner: manu
tags: [hive, lesson, monkey-patch, mcp, forward-compat, issue-75]
---

# Forward-compatible monkey-patches must be self-gated on the failure mode

**Context:** Patching `mcp.shared.session.RequestResponder.__exit__` to fix the issue #75 cancellation leak. Upstream will eventually fix it; we don't want our patch to mask a different bug if upstream changes the internal flow.
**Problem:** A naive monkey-patch overrides upstream behaviour permanently. Once we ship it, every future bug in that method becomes invisible (or worse, the patch's logic conflicts with a new upstream fix and creates a *new* bug).
**Solution:** Gate the patch on the exact failure-mode signature: `if self._completed and isinstance(exc, anyio.get_cancelled_exc_class())`. The first clause says "we already sent a response" — the second says "this is anyio's cancellation". Anything else re-raises normally. Wrap the patch application in a defensive `try`/except that logs a warning if `RequestResponder` was renamed or restructured upstream.
**Why:** A self-gated patch becomes inert the moment upstream lands a fix — the trigger condition simply stops being reachable. That makes the monkey-patch removable without coordination: even if we forget to drop it, it doesn't do anything harmful in the post-fix world. Defensive import keeps the production server from crashing if upstream renames the class.
**Tags:** `#monkey-patch` `#mcp` `#forward-compat` `#issue-75`
