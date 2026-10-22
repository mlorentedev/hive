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
arguments: `uvx --no-build-package cryptography hive-vault`. Setting the
environment variable works too, in the environment of the process that runs
`uv`:

```powershell
$env:UV_NO_BUILD_PACKAGE = "cryptography"
```

`hive self-upgrade` needs no flag of its own: it builds each version with
`uv pip install --only-binary cryptography` (`_runtime.build_version`). The
`uvx` that starts it still needs the flag, because uvx installs hive-vault
before `hive` runs.

## Security caveat

The cryptography that Arm64 gets is not the current one. On 2026-10-22 the
flag resolves 46.0.3, and 46.0.3 is inside the affected range of these
advisories:

| Advisory | Severity | Fixed in | Affects |
|---|---|---|---|
| GHSA-r6ph-v2qm-q3c2 | high | 46.0.5 | EC public keys on SECT curves |
| GHSA-537c-gmf6-5ccf | high | 48.0.1 | the OpenSSL bundled in every wheel |
| GHSA-jwv3-5hgf-82ww | high | 49.0.0 | X.509 path building |
| GHSA-g6cj-pr64-35w5 | high | 50.0.0 | PKCS#7 EnvelopedData decryption |
| GHSA-m2h6-j472-rp4c | medium | 49.0.0 | X.509 verifier name constraints |
| GHSA-p423-j2cm-9vmq | medium | 46.0.7 | APIs given non-contiguous buffers |

hive itself calls cryptography only to generate and load its own P-256
self-signed certificate (`_identity.py`), and none of the API-specific
advisories touch those calls. The bundled-OpenSSL one applies to every
wheel below 48.0.1, and hive cannot rule it out. Other packages in the same
environment also use cryptography: authlib, joserfc and pyjwt, through
fastmcp. The daemon authenticates with a static bearer token
(`StaticTokenVerifier`), not with OAuth or JWT signatures, so those code paths
are not on by default.

The upgrade trigger is the first `win_arm64` wheel at or above 50.0.0 on
PyPI. The flag picks it up with no change in hive.

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
