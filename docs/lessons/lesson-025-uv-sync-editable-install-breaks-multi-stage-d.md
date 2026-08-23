---
id: lesson-025-uv-sync-editable-install-breaks-multi-stage-d
type: lesson
status: active
created: "2026-03-10"
owner: manu
tags: [hive, lesson, docker, uv, multi-stage, python-packaging]
---

# uv sync editable install breaks multi-stage Docker builds

**Context:** Building a multi-stage Docker image for hive-vault. The builder stage used `uv sync --frozen --no-dev` to install the local package, then only `.venv` was copied to the final stage.
**Problem:** Runtime `ModuleNotFoundError: No module named 'hive'`. `uv sync` installs the local project as an editable/direct-url reference pointing to `/app/src`, which doesn't exist in the final image (only `.venv` is copied).
**Solution:** Use `uv sync --frozen --no-dev --no-install-project` for third-party deps only, then `.venv/bin/pip install --no-cache-dir --no-deps .` to install the local package as a proper wheel embedded in `.venv/lib/`. The wheel is self-contained — no reference to source paths.
**Why:** `uv sync` optimizes for development (editable installs are faster for iteration). In multi-stage Docker builds where source isn't copied to the final stage, you need a non-editable wheel. This is a `uv`-specific gotcha — `pip install .` has always produced non-editable installs by default.
**Tags:** `#docker` `#uv` `#multi-stage` `#python-packaging`
