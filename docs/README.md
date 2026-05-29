# Documentation

Project-bound knowledge for `hive`, kept in-repo (docs-as-code). The *operate/build* layer lives here so it is versioned with the code and readable by any agent in-context — no external knowledge store required.

- [`adr/`](adr/) — Architecture Decision Records (the *why* behind decisions), plus [`adr/sequence-diagrams.md`](adr/sequence-diagrams.md) for the end-to-end flow diagrams
- [`runbooks/`](runbooks/) — operational procedures (e.g. the architecture audit checklist)
- [`troubleshooting/`](troubleshooting/) — known issues, root-cause write-ups, and fixes
- [`lessons.md`](lessons.md) — accumulated gotchas and post-mortems

The *decide/position* layer (roadmap, prestudy, strategy) and session memory live in the maintainer's cross-project knowledge store and are intentionally not committed here.
