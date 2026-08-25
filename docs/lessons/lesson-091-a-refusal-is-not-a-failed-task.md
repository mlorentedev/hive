---
id: lesson-091-a-refusal-is-not-a-failed-task
type: lesson
status: active
created: "2026-08-23"
owner: manu
tags: [hive, lesson, error-handling, api-design, classification, dispatcher, exit-codes, HIVE-384]
---

# A refusal is not a failed task, and the caller acts on the difference

**Context:** `hive delegate` was built as a dispatch seam a router can call: it exits `3` when the pool did not serve the request, and `1` when the pool served it and the answer is unusable. The distinction exists so a dispatcher knows whether to advance to the next entry in its fallback chain or stop.
**Problem:** `clients.py` raised `RuntimeError` for **every** non-2xx status. A `429` — the pool saying "not now, ask me later or ask someone else" — arrived at the caller wearing the same type as a malformed request. The verb then mapped it to *task failed*, exit `1`, and a dispatcher would have **stopped its chain exactly where it should have advanced**. The same collapse hit `401`/`403`: a credential the pool rejected is a pool that never ran the task, not a task that ran and failed.
**Solution:** Split the exception hierarchy at the axis the caller cares about. `PoolUnavailableError(ConnectionError)` covers *did not serve*: transport failure, timeout, `429`, `401`, `403`. `RuntimeError` stays for *served, unusable*: a 4xx about the request itself, any 5xx, a non-JSON body. Any other 4xx or 5xx falls closed to task-failed, because inventing a retry is worse than declining one. Note what the boundary does *not* classify: a 3xx matches neither branch and goes on to `resp.json()`, reaching task-failed only because a redirect body fails to parse — the safe answer, arrived at by accident rather than by rule. `tests/test_pool_classification.py` pins each status to its class.
**Why:** An error taxonomy is not a description of what went wrong — it is an **instruction to the caller about what to do next**. Grouping by where the failure was detected (this HTTP call raised) rather than by what the recipient must decide (retry elsewhere, or stop) produces a hierarchy that is accurate and useless. The tell is that the fix required no new information: the status code was always in the response. Only the classification was wrong, and a classification is only wrong relative to a decision someone downstream has to make. Cousin of [[docs/lessons/lesson-095-a-client-that-always-exists|lesson-095]] — both are surfaces that were internally consistent and told the caller the wrong thing.
**Tags:** `#error-handling` `#api-design` `#classification` `#dispatcher` `#exit-codes` `#HIVE-384`
