---
id: lesson-067-dependabot-secrets-are-isolated-from-actions-
type: lesson
status: active
created: "2026-07-07"
owner: manu
tags: [hive, lesson, ci, github-actions, secrets, dependabot, bitacora, OPS-002, projects-v2]
---

# Dependabot secrets are isolated from Actions secrets — exclude the trigger, don't duplicate the PAT

**Context:** The bitácora `add-to-project.yml` workflow (OPS-002 canonical copy, rolled out across repos) adds every opened issue + PR to the cross-repo GitHub Project #1. It started failing on every Dependabot PR with `GH_TOKEN: ` empty and `##[error]Bad credentials`, while the issues path worked fine after a PAT rotation.
**Problem:** GitHub isolates Dependabot secrets from Actions secrets — a workflow run triggered by Dependabot can only read the Dependabot secret store, not the Actions store. `BITACORA_PAT` existed only as an Actions secret, so under a Dependabot trigger `secrets.BITACORA_PAT` resolved to empty and the GraphQL `addProjectV2ItemById` call failed auth. The fix is NOT to duplicate the PAT into a second store (doubles the rotation surface across every repo in the rollout) — it is to stop triggering the workflow for Dependabot at all.
**Solution:** Exclude Dependabot PRs from the board trigger in the job `if:` (`github.event.pull_request.user.login != 'dependabot[bot]'`). A version bump is not work to triage — its record already lives in CHANGELOG (release-please) + git history, so it does not belong on the bitácora board. Excluding the trigger means only the Actions secret is required, halving the rotation surface. The issues path still uses `secrets.BITACORA_PAT` (Actions store) and works. Note: Projects v2 fields (Status/Type/Priority) are GraphQL-only — there is no REST — and `addProjectV2ItemById` is idempotent, returning the existing item id if the issue is already on the board (use it to fetch an item id in one call instead of paginating the whole board).
**Why:** For a user account (not an org) there are no org-level secrets, so the Actions-vs-Dependabot split is unavoidable per-repo. The scalable move is to minimize the number of stores you must rotate, not to populate all of them. Excluding a non-triage event from a triage board is a hygiene win that also removes a secret store — two benefits from one `if:` clause.
**Tags:** `#ci` `#github-actions` `#secrets` `#dependabot` `#bitacora` `#OPS-002` `#projects-v2`
