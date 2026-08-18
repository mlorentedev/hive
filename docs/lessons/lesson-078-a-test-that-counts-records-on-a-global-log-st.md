---
id: lesson-078-a-test-that-counts-records-on-a-global-log-st
type: lesson
status: active
created: "2026-08-08"
owner: manu
tags: [hive, lesson, testing, pytest, caplog, flaky-tests, concurrency, logging, HIVE-322]
---

# A test that counts records on a global log stream is order-dependent by construction

**Context:** `make check` came back red on `tests/test_lock_telemetry.py::test_filelock_with_telemetry_timeout_emits_and_reraises` while adding an unrelated test to the HIVE-322 branch. The test passed in isolation, passed in a two-file run with the new test, and passed in three subsequent full-suite runs — two plain, one under coverage.
**Problem:** All four tests in the file end with `matching = [r for r in caplog.records if "mcp.lock_contention" in r.getMessage()]` followed by `assert len(matching) == 1`. `caplog` captures the whole `hive._helpers` stream for the process, **across every thread**, while each test controls only its own lock. Any concurrent acquire anywhere in the interpreter — a reconciler or outbox thread taking `_git_filelock` — turns an expected 1 into a 2 and fails a test that did nothing wrong. The exposure is wildly uneven and that unevenness is the diagnostic: the two *success* tests acquire in microseconds, while the two *timeout* tests deliberately hold a 0.1s window open. Four orders of magnitude more room for a foreign record to land, and it was one of the timeout tests that broke.
**Solution:** Scoped the filter to the lock under test via a `_contention_messages(caplog, lock_name)` helper matching `f"lock={lock_name} "` — the trailing space pins it to the `lock=%s waited_ms=%d` format so a short name cannot match a longer one that starts with it. Proved the mechanism rather than assuming it: a throwaway pytest plugin running a background thread that emits a foreign `mcp.lock_contention` record fails **both timeout tests** under the old filters, including exactly the one `make check` hit, and passes all 13 under the scoped ones. Note the scoping narrows *which* records are counted, never how many are required, so a genuine regression that drops or duplicates the record still fails, and a failure from any other cause is left exposed rather than masked.
**Why:** An assertion on an exact count is an assertion about everything the process did, not just about the code under test — the moment the observed stream is shared, the test has an implicit dependency on every other thread in the interpreter. Logging fixtures make this especially easy to get wrong because they *look* scoped: `caplog.at_level(logging.INFO, logger="hive._helpers")` names a logger and reads like a filter, but it sets a level, it does not restrict authorship. Whenever a test counts events on a stream it does not exclusively own, filter by an identifier it does own. And when the suspected mechanism is concurrency, reproduce it by injecting the interference deliberately — a flake that only appears once in four full runs will not be understood by re-running it, and "it passed this time" is not evidence.
**Tags:** `#testing` `#pytest` `#caplog` `#flaky-tests` `#concurrency` `#logging` `#HIVE-322`
