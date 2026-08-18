---
id: lesson-082-a-closed-pull-request-leaves-no-trace-in-the-
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, github, upstream, research, verification, mcp, HIVE-127]
---

# A closed pull request leaves no trace in the issue it would have fixed

**Context:** A handoff recorded that upstream `modelcontextprotocol/python-sdk#2416` had been "claimed 2026-07-11 with no PR since", and on that basis the next step was a courtesy ping to the claimant before offering our own fix.
**Problem:** The premise was false when it was written. A contributor had claimed the issue *and opened a PR the same day*; they closed it themselves eight days later. Two further community PRs had been opened and closed by a core contributor without comment, and a fourth sat open but untouched since April — four attempts, none merged. None of this is visible from the issue: a PR that references an issue and is then closed unmerged leaves the issue's comment thread unchanged, so the claimant's "I'll open a PR shortly" comment remains the last visible event and reads as an unfulfilled claim. Acting on the stale premise would have meant posting a courtesy ping to someone who had already withdrawn, while missing the actually decision-relevant signal: a maintainer silently closing community PRs on this issue.
**Solution:** Queried the PRs directly — `gh api 'search/issues?q=repo:<owner>/<repo>+is:pr+<issue-number>+in:body'` — then checked each one's state, author and closing actor. That turned "nobody is working on it" into a four-row history with a visible pattern. The outreach was reshaped from a ping into a maintainer-directed question (would a fix be accepted, and against which base), and the corrected timeline was written back onto our own tracking issue so the dead premise could not propagate a third time.
**Why:** An issue thread is not a record of the work attempted on it. Closed-unmerged PRs are invisible there, so "no PR" inferred from reading the issue means "no PR that is still open or was merged" — a much weaker statement, and the difference inverts what the right next action is. Search the PR space separately before concluding a lane is empty. The broader habit this reinforces: when a handoff hands you a claim about *someone else's* repo, re-derive it before acting outward — outward actions under a user's identity have no undo, and third-party state is exactly the kind that moves between sessions.
**Tags:** `#github` `#upstream` `#research` `#verification` `#mcp` `#HIVE-127`
