---
id: lesson-099-a-patch-tested-against-a-stand-in-cannot-report-its-own-absence
type: lesson
status: active
created: "2026-09-24"
owner: manu
tags: [hive, lesson, testing, monkey-patch, dependencies, silent-failure, mcp, ci, uv, HIVE-434]
---

# A patch tested against a stand-in cannot report its own absence

**Context:** `src/hive/_compat.py` patched the private `mcp.shared.session.RequestResponder.respond` so a response produced after cancellation was dropped instead of tripping `assert not self._completed` and killing the server (python-sdk#2416). The `mcp>=1.27,<2.0` cap existed to keep that private target in place. Dependabot widened the cap to `<3.0` (#369), and #421 widened `fastmcp` to `<5`, which pulls in mcp 2.x. How the widening got through is recorded in `pattern-dependabot-ci` §5.
**Problem:** The PyPI install (4.2.1) and a Windows install (#437) ran mcp 2.2.0, where `RequestResponder` does not exist. `_compat.apply()` logged a warning and did nothing. Two separate things kept this out of CI:
1. **CI never ran 2.x.** Its install step (`uv pip install -e .`) resolved mcp 2.2.0 fresh from `pyproject.toml`, but the first `uv run` silently re-synced the venv to `uv.lock` ("Uninstalled 48 packages, Installed 52"), which still pinned mcp 1.28.1. The install log showed one resolution and the tests ran on another. #434 first misread that log as "CI is green on 2.2.0"; it was corrected on 2026-09-24.
2. **The shim's own tests could not have noticed.** `tests/test_compat_shim.py` exercised the patch against `_FakeResponder`, a stand-in class that always exists, so it passes whether or not the real target is there. The one test that spawned a real server carried the `diagnostic` marker (lesson 097), so it did not gate.

When the suite finally ran on 2.2.0 it showed what the 1.x runs had hidden: 4 failures and 1 module that could not be collected. The worst was a production regression. mcp 2.x clients open a session with `server/discover` instead of `initialize`, so the daemon's `/status` reported `sessions_started: 0` for every such client.
**Solution:** The owner chose to adopt mcp 2.x on purpose (#434, 2026-09-24) instead of re-narrowing to 1.x, which upstream has put in maintenance mode. The evidence is the python-sdk#2416 repro, run three times against each server: 3/3 crashes on lowlevel mcp 1.30.0, 3/3 on FastMCP 3.3.1 with mcp 1.28.1 and no shim, and 0/3 on FastMCP 4.0.9 with mcp 2.2.0. The 2.x dispatcher never answers a cancelled request. The fix:
- bounds `mcp>=2.2,<3` and `fastmcp>=4,<5`, asserted by `tests/test_dependency_bounds.py` (including the version the suite actually runs on);
- the patch deleted;
- `tests/test_cancel_race.py` drives hive's own server with an uncancellable sync tool. It fails on 1.x without the patch and passes on 2.x. A fast tool such as `vault_list` never reaches the race, so the first draft of this test passed even with the patch removed;
- CI installs with `uv sync --locked`;
- sessions are counted on either handshake.
**Why:** **A test for a patch has to fail when the patch's real target is missing, and CI has to run on what users install.** A stand-in keeps the patch logic tested, but it also hides the one failure that matters: the target moving or disappearing. Pair a stand-in unit test with a check that imports the real symbol, or better, test the behaviour the patch exists for, because that test keeps its meaning after the patch is deleted. Separately, a CI log proves the resolution the *tests* ran on only if install and run read the same source: install from the lock with `--locked`, and assert the installed version inside the suite. A regression test must also be shown to fail without the fix. The first draft here did not, and nothing else would have said so.
**Tags:** `#testing` `#monkey-patch` `#dependencies` `#silent-failure` `#mcp` `#ci` `#uv` `#HIVE-434`
