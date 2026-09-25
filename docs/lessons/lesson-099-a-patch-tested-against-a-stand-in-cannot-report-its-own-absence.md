---
id: lesson-099-a-patch-tested-against-a-stand-in-cannot-report-its-own-absence
type: lesson
status: active
created: "2026-09-24"
owner: manu
tags: [hive, lesson, testing, monkey-patch, dependencies, silent-failure, mcp, HIVE-434]
---

# A patch tested against a stand-in cannot report its own absence

**Context:** `src/hive/_compat.py` patches the private `mcp.shared.session.RequestResponder.respond` so a response produced after cancellation is dropped instead of tripping `assert not self._completed` and killing the server (python-sdk#2416). The `mcp>=1.27,<2.0` cap existed to keep that private target in place. Dependabot widened the cap to `<3.0` (#369), and #421 widened `fastmcp` to `<5`, which pulls in mcp 2.x. How the widening got through is recorded in `pattern-dependabot-ci` §5.
**Problem:** On 2026-09-23 CI, the PyPI install (4.2.1) and a Windows install (#437) all ran mcp 2.2.0, where `RequestResponder` does not exist. `_compat.apply()` logged a warning and did nothing, and CI stayed green. The gating tests in `tests/test_compat_shim.py` exercise the patch against `_FakeResponder`, a stand-in class that always exists, so they pass whether or not the real target is there. The one test that spawns a real server is behind the `diagnostic` marker (lesson 097), so it does not gate. Nothing in the build could see that the protection was gone.
**Solution:** The owner chose to adopt mcp 2.x on purpose (#434, decision recorded 2026-09-24) instead of re-narrowing to 1.x, which upstream has put in maintenance mode. The evidence is the python-sdk#2416 repro (call a 1 s sync tool, abandon a second call after 50 ms, call again), run three times against each server: 3/3 crashes on lowlevel mcp 1.30.0, 3/3 on FastMCP 3.3.1 with mcp 1.28.1 and no shim, and 0/3 on FastMCP 4.0.9 with mcp 2.2.0. The 2.x dispatcher (`mcp/shared/jsonrpc_dispatcher.py`) never answers a cancelled request, so the crash is gone by design. The planned fix, not yet shipped:
- raise the lower bounds so 1.x cannot resolve once the shim is gone (`mcp>=2.2,<3`, `fastmcp>=4,<5`);
- delete the shim;
- add a test that fails if either bound leaves its audited major;
- port the repro as a gating regression test against the real stack;
- install CI from the lock.
**Why:** **A test for a patch has to fail when the patch's real target is missing.** A stand-in keeps the patch logic tested, but it also hides the one failure that matters: the target moving or disappearing. For a patch whose own code degrades quietly, that means the protection can vanish while every check stays green. Pair any stand-in unit test with a check that imports the real symbol. Better still, test the behaviour the patch exists for, because that test keeps its meaning even after the patch is deleted.
**Tags:** `#testing` `#monkey-patch` `#dependencies` `#silent-failure` `#mcp` `#HIVE-434`
