---
id: lesson-077-shutil-which-proves-a-name-resolves-not-that-
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, cli, packaging, windows, uv, service, preconditions, verification, HIVE-328]
---

# `shutil.which` proves a name resolves, not that the thing behind it runs

**Context:** `hive service install` registers the daemon with the platform supervisor (systemd / Task Scheduler), and `_service._resolve_exec()` supplied the executable path it registers by returning `shutil.which("hive")` unconditionally.
**Problem:** `which` answers "is there a file named `hive` on PATH", which is not the question a supervisor needs answered. On the maintainer's Windows box `shutil.which("hive")` happily returned `C:\Users\...\.local\bin\hive.exe`, while running it produced `error: uv trampoline failed to canonicalize script path` — an orphaned uv trampoline is *present but dead*, and at the `which` level it is byte-for-byte indistinguishable from a healthy console script. So the install would register a supervised daemon whose task action can never start, and Task Scheduler would report the registration as **successful**. The failure surfaces later, from a different component, with nothing pointing back at the install that caused it. This was the upstream half of an outage where four mechanisms went silently inert together.
**Solution:** Make the resolver answer the question actually being asked — verify the candidate *starts* before handing it to a supervisor, and refuse to register otherwise. Shipped as PR1 of `#328`; PR2 (the PATH launcher that stops producing dead trampolines in the first place) is a separate design decision written up in `specs/HIVE-328-runtime-launcher/proposal.md`, so the fix is deliberately split: stop registering broken binaries now, fix why they exist next.
**Why:** Existence checks are the cheapest thing to reach for and almost never the predicate you mean. `which`, `Path.exists()`, `importlib.util.find_spec` and friends all confirm that a *name resolves* — the question behind a registration, a health check or a precondition is nearly always whether the thing *works*. The gap between the two is exactly where silent inertness lives, because the check passes at the moment of registration and the failure is deferred to a boundary nobody is watching. Whenever a check gates handing something to another system that will not re-validate it, spend the subprocess call and execute it.
**Tags:** `#cli` `#packaging` `#windows` `#uv` `#service` `#preconditions` `#verification` `#HIVE-328`
