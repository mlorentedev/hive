---
id: lesson-070-a-removed-module-error-from-a-metadata-only-m
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, dependencies, packaging, fastmcp, windows, diagnosis, HIVE-319]
---

# A "removed module" error from a metadata-only meta-package, and a floor no install could satisfy

**Context:** [#319](https://github.com/mlorentedev/hive/issues/319) reported hive MCP failing at startup on Windows with `ModuleNotFoundError: No module named 'fastmcp.server.tasks.routing'`, attributed in the issue to FastMCP API drift or an insufficient dependency bound.
**Problem:** The module was never removed. Inspecting the published `fastmcp-slim` wheels for 3.3.1, 3.4.0, 3.4.2 and 3.4.6 showed `fastmcp/server/tasks/routing.py` present in all four, byte-identical file sets under `fastmcp/server/tasks/`, and a clean venv with `hive-vault==1.43.0` + fastmcp 3.4.6 imported `hive.server` fine. The reported hypothesis was falsifiable in about two minutes and false. The real hazard is packaging: from 3.x, **`fastmcp` on PyPI is a metadata-only meta-package** — its wheel contains 4 files, none of them code — that pins `fastmcp-slim[client,server]==<same version>`, with all code shipping in `fastmcp-slim` under the shared `fastmcp/` namespace. Two distributions writing into one import namespace means a partial or version-skewed upgrade leaves an install that imports but is missing real submodules. That is the shape of the reported error, and on Windows it is the known [#267](https://github.com/mlorentedev/hive/issues/267) failure class (an in-use install `uv` cannot replace). Separately, the audit found `pyproject.toml` declared `fastmcp>=2.0.0` — a floor **no working install could satisfy**, since `_client.py` imports `fastmcp.server.providers.proxy.FastMCPProxy`, which only exists from 3.x (2.x had it at `fastmcp.server.proxy`).
**Solution:** Bounded to `fastmcp>=3.3.1,<4` ([#320](https://github.com/mlorentedev/hive/pull/320)), floor set to the lowest version actually verified rather than the lowest that might work, suite green on both ends. The issue was answered with the falsification plus the three commands that discriminate a skewed install (`uv tool list`, `pip show fastmcp fastmcp-slim`, `python -c "import fastmcp; print(fastmcp.__file__)"`) — the decisive signal being whether `fastmcp` and `fastmcp-slim` report the same version.
**Why:** Check whether a "removed" symbol was actually removed before believing the report — downloading the wheels is cheaper than reasoning about upstream intent, and here it inverted the diagnosis. And when a dependency splits its code into a companion distribution, the version range in `pyproject.toml` stops describing what will be installed: the meta-package's own pin does. A range that cannot produce a working install is worse than no range, because it reads like a tested guarantee.
**Tags:** `#dependencies` `#packaging` `#fastmcp` `#windows` `#diagnosis` `#HIVE-319`
