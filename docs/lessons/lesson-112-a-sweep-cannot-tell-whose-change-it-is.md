---
id: lesson-112-a-sweep-cannot-tell-whose-change-it-is
type: lesson
status: active
created: "2026-10-22"
owner: manu
tags: [hive, lesson, git, concurrency, HIVE-510]
---

# A sweep cannot tell whose change it is

**Context:** `vault_commit` is the explicit flush, and ADR-018 §3 keeps it as
the one sanctioned sweep of the working tree: it is the remediation for paths
no tick committed. It ran `git add -A` and then a bare `git commit` (#510).

**Problem:** #470 fixed the same hazard on the write path, but a sweep has no
list of paths to scope to. It committed another session's staged work under
this session's message. Narrowing it to "this process's queued paths" looked
like the fix and was nearly a no-op: the reconciler drains that queue every
tick, so by flush time it is usually empty, and the narrowing would have
removed the AC10 remediation outright.

**Solution:** Keep the sweep, but take only what is *not* in the index:
`git status --porcelain -z --untracked-files=all`, keep `??` and `␠Y`
entries, then `add` and `commit --only` reading `:(top,literal)` pathspecs
from a NUL-separated file. The file keeps a sweep of hundreds of paths under
Windows' command-line limit; `top` because porcelain paths start at the
repository root; names decoded with `surrogateescape` so a non-UTF-8 name
survives the round trip. A path with any staged change (including `MM`) is
left out and named in the result, both halves for a rename.
Naming matters: a write killed between `git add` and `git commit` leaves
hive's *own* path staged, and git cannot tell it from another session's.

**Rule:** Git state records *what* changed, never *who* changed it. A sweep
can exclude a state (staged) but not an owner. When the cause is ownership,
say which part a state-based filter cannot close and track it (#527) rather
than calling the class fixed.
