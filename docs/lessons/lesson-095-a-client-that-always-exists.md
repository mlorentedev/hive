---
id: lesson-095-a-client-that-always-exists
type: lesson
status: active
created: "2026-08-23"
owner: manu
tags: [hive, lesson, health-checks, observability, optional-dependency, api-design, silent-failure, HIVE-384]
---

# A client that always exists reports a pool that never answers

**Context:** The whole of HIVE-384 exists because of this defect, and it is worth separating from the work it caused. `worker_status` was the surface that answered "can hive delegate inference?" — and it answered by inspecting the client object.
**Problem:** `ServerContext.worker` was **non-optional**: a client was always constructed, so it was always present, so every surface that asked "is the worker available?" got yes. Meanwhile the endpoint behind it served **zero reachable models** — one backend was not running locally, the other had been retired upstream — and had done so for an unknown length of time. Nothing noticed, because nothing had ever asked the endpoint anything. The consequence was not cosmetic: it made hive ineligible as a backend of the `dotf agent run` executor, and the reason was invisible from inside hive, whose own health output said everything was fine.
**Solution:** Make absence representable. `worker: OpenAICompatibleClient | None`, where `None` means *unconfigured* — the same meaning `embed_base_url == ""` already carried for the semantic backend — and callers must branch on it rather than assume a client exists. An empty `HIVE_WORKER_BASE_URL` disables the worker instead of guessing an endpoint, because a worker that silently points somewhere is worse than one that says it is unconfigured. Reachability becomes a **probe with its own short connect timeout**, reported separately from configuration: `vault_health` now emits `{"worker": false, "worker_reachable": "unprobed"}`, three states rather than two.
**Why:** **Constructed is not configured, configured is not reachable, and a health surface that conflates them will report health it never measured.** The failure was designed in: making the field non-optional removed the only place the type system could have said "there may be nothing here", so every consumer was *correct* to assume presence. The type was the bug, and the missing probe was only how it became visible. Two rules fall out. First, when a dependency is genuinely optional, the type must say so — `T | None` is not defensive clutter, it is the fact. Second, a health check must **do the thing it reports on**: send the request, take the timeout, report what came back. Anything cheaper reports on hive's own initialisation, which was never in doubt. Directly cousin to [[docs/lessons/lesson-077-shutil-which-proves-a-name-resolves-not-that-|lesson-077]] (`shutil.which` proves a name resolves, not that the thing behind it runs) and to the wider family in [[docs/lessons/lesson-094-a-verification-command-that-selects-zero-tests|lesson-094]].
**Tags:** `#health-checks` `#observability` `#optional-dependency` `#api-design` `#silent-failure` `#HIVE-384`
