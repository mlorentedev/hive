---
id: lesson-110-refuse-the-source-build-instead-of-pinning
type: lesson
status: active
created: "2026-10-22"
owner: manu
tags: [hive, lesson, packaging, uv, windows, arm64, BUG-012]
---

# Refuse the source build instead of pinning

**Context:** hive-vault could not install on Windows Arm64 (#515). It requires
`cryptography>=42`, and cryptography stopped shipping `win_arm64` wheels after
46.0.3.

**Problem:** A floor with no ceiling resolves to the newest release, whether
or not it has a wheel for the platform. When it has none, the installer builds
from source, and a clean machine has no compiler. A platform marker
(`cryptography<47` on Arm64) would fix it for every installer, but it would
keep Arm64 on 46.x after upstream ships Arm64 wheels again.

**Solution:** Refuse the source build for that one package: `--only-binary
cryptography` in `uv pip install`, `--no-build-package cryptography` for
`uvx` and `uv tool install`. The resolver backtracks to the newest release
that has a wheel, and moves forward again on its own once a newer one does.
hive passes the flag only on Windows Arm64. cryptography ships no wheel at
all for some platforms (Intel macOS, riscv64, 32-bit Windows in 50.0.2), and
there refusing the source build turns a working build into a resolution
failure, or quietly moves the version back. A flag scoped to the package
still has to be scoped to the platform where the build is known to fail.

The flag does not avoid the stale version; it avoids locking it in. Today
Arm64 lands on 46.0.3, which has six advisories fixed in later releases
(four rated high by the GitHub Advisory Database, one by pyca itself). A downgrade like that has to be written down where the
installer is documented, with the advisories it carries and the event that
ends it, or nobody notices it.

**Limit:** A flag only reaches the installs that pass it. hive passes it in
`hive self-upgrade`. The `uvx` that launches `hive self-upgrade`, and every
`uvx hive-vault` registration, run uv before any hive code, so the caller
must pass the flag there.

**Testing notes:** `uv pip compile --python-platform aarch64-pc-windows-msvc`
shows the resolution from any host. `uvx --python-platform` installs
cross-platform wheels on macOS, so its environment shows the wheel tag
(`cp311-abi3-win_arm64`) even though nothing in it can run. Neither proves
the install on Arm64; only an Arm64 machine does.
