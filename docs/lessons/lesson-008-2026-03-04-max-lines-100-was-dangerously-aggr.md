---
id: lesson-008-2026-03-04-max-lines-100-was-dangerously-aggr
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-04: max_lines=100 was dangerously aggressive

- **Context:** Default `max_lines` was 100. Benchmarking suite revealed that real vault files (roadmap, tasks, lessons) are 125-173 lines. At max_lines=100, tools captured only 7-22% of actual content.
- **Impact:** `session_briefing` was silently truncating critical information. Users had no signal that they were missing content.
- **Fix:** Raised default to 500. At 500 lines, content capture is 98-100% for all existing vault files.
- **Lesson:** Never pick defaults by intuition. Benchmark against real data first.
