---
id: "hive-515-windows-arm64-install"
type: troubleshooting
status: active
severity: high
tags: [install, windows, arm64, cryptography, uv]
created: "2026-10-22"
owner: manu
---

# hive-vault fails to install on Windows Arm64

## Symptoms

Any install of `hive-vault` on Windows on Arm64 stops while building
`cryptography`:

```
Python reports SOABI: cp313-win_arm64
   Building cryptography==50.0.2
error: linker `link.exe` not found
```

This happens with `uv tool install hive-vault`, `uvx hive-vault` in an MCP
registration, and `uvx --from hive-vault hive self-upgrade <version>`.

## Cause

hive-vault requires `cryptography>=42`, so the resolver picks the newest
release. cryptography shipped `win_arm64` wheels only in 46.0.0–46.0.3, and
later releases ship `win_amd64` wheels only (checked 2026-10-21). With no
wheel, uv builds from source, and a fresh machine has no MSVC linker (#515).

## Fix

Tell uv not to build cryptography. The resolver then falls back to the
newest release that has a wheel for the platform (46.0.3 today):

```powershell
uv tool install --no-build-package cryptography hive-vault
uvx --no-build-package cryptography --from hive-vault hive self-upgrade <version>
```

For an MCP registration that runs `uvx hive-vault`, add the same flag to its
arguments: `uvx --no-build-package cryptography hive-vault`.
`UV_NO_BUILD_PACKAGE=cryptography` works too, but it must be set in the
environment of the process that runs `uv`.

`hive self-upgrade` needs no flag of its own: it builds each version with
`uv pip install --only-binary cryptography` (`_runtime.build_version`). The
`uvx` that starts it still needs the flag, because uvx installs hive-vault
before `hive` runs.

## Verification

From any host, check which cryptography uv picks for Arm64:

```bash
echo hive-vault > req.in
uv pip compile req.in --python-platform aarch64-pc-windows-msvc \
  --python-version 3.13 --only-binary cryptography
```

It prints `cryptography==46.0.3`. Without `--only-binary` it prints a release
that has no `win_arm64` wheel. The install itself is proven only on an Arm64
machine.

Once cryptography publishes `win_arm64` wheels again, the flag picks them up
with no change here.
