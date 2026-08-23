---
id: lesson-009-2026-03-04-benchmark-driven-defaults-over-gut
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-04: Benchmark-driven defaults over gut feeling

- **Context:** Both `max_lines` (100) and budget cap ($5/mo) were set by rough estimation during initial development.
- **Decision:** Built a benchmarking suite that measures content capture ratio, token savings, and latency across real vault files.
- **Outcome:** max_lines raised 5x (100 to 500), budget lowered 5x ($5 to $1). Both changes backed by data, not guesswork.
- **Lesson:** For any configurable default, write a characterization benchmark before picking the value. The cost of being wrong silently is higher than the cost of measuring.
