---
id: lesson-089-a-release-that-fails-on-a-rate-limit-reports-
type: lesson
status: active
created: "2026-08-09"
owner: manu
tags: [hive, lesson, ci, release, github-actions, rate-limit, observability, skipped-is-not-passed, release-please]
---

# A release that fails on a rate limit reports its publish jobs as `skipped`, and the quota is shared with your own CLI

**Context:** The `Release` workflow runs on every push to master: `release-please` first, then `publish` and `publish-mcp` as `needs:`-dependents. It failed three separate times in one day, each time with `API rate limit already exceeded for user ID 13562150`.
**Problem:** Two mechanisms compound, and each hides the other. **First, the failure is shaped like a non-event.** When `release-please` fails, its dependents do not fail — they report `skipped`, because that is what `needs:` does. So the run summary shows one red job and two grey ones, and the grey ones are the two that would have told you nothing shipped. In the morning's occurrence this produced a **13-hour silent half-release**: `pyproject.toml` said 2.0.0, no tag, no GitHub Release, PyPI still on 1.43.1, and nothing anywhere said "not published". **Second, the quota is shared with the humans and agents using `gh`.** The workflows authenticate as user 13562150, and so does the maintainer's (and any agent's) `gh` CLI. A session that makes heavy API use — listing runs, creating PRs, querying the project board — draws down the same 5,000/hour bucket the release pipeline needs. The evening's two failures came *after* a session had pushed `used` to 5015/5000; `release-please` then lost a coin flip it never knew it was in.
**Solution:** Rerunning the failed job is the whole fix (`gh api repos/OWNER/REPO/actions/runs/<id>/rerun-failed-jobs --method POST`) — nothing is corrupted, the release simply did not happen. Detection is the real work: after any merge to master, check the `Release` run's own conclusion rather than scanning for red, because `skipped` is what the interesting jobs will say. Note also that **GraphQL and REST have separate buckets** — when GraphQL is exhausted, `gh pr create` fails while `POST /repos/{o}/{r}/pulls` still works, which is a way to keep moving rather than a way to ignore the limit.
**Why:** `skipped` is the most dangerous CI state, because it is the only one that means "this did not run" while looking like "this had nothing to do". Any `needs:`-chain converts an upstream failure into downstream silence, so the jobs that assert the valuable outcome are exactly the ones that go quiet. The shared-quota half generalises further: an agent's read-only reconnaissance is not free, and it is drawn from the same allowance as the automation the repository depends on. When a pipeline authenticates as a user rather than as an app, treat that user's API budget as a shared production resource.
**Tags:** `#ci` `#release` `#github-actions` `#rate-limit` `#observability` `#skipped-is-not-passed` `#release-please`
