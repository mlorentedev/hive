---
id: lesson-098-a-migration-must-reach-the-writers-not-only-the-readers
type: lesson
status: active
created: "2026-09-22"
owner: manu
tags: [hive, lesson, migration, knowledge-placement, silent-failure, capture-lesson, HIVE-431]
---

# A migration must reach the writers, not only the readers

**Context:** The knowledge-placement model (KPM-001) moved each project's lessons out of the vault into the repository's `docs/lessons/`, and deleted `10_projects/<project>/90-lessons.md` as a migrated remnant. On 2026-09-22 a lesson for kubelab went through `capture_lesson(project="kubelab", ...)`.
**Problem:** The tool recreated `10_projects/kubelab/90-lessons.md` (undone by hand in vault commit `ea26c36e`) and answered `Lesson captured`. When the migration landed, the read side had been taught the new layout (`_read_existing_lessons_text` already looks at `docs/lessons.md`), but the write side still had a hard-coded path plus a create-if-missing branch. A file deleted on purpose looks exactly like a file that was never there, so the writer "helpfully" undid the migration every time it ran, and the success message meant the caller never found out.
**Solution:** `capture_lesson` now only appends (#431). A missing `90-lessons.md` returns an error that writes nothing and names where the lesson belongs: the repository's `docs/lessons/` for a project lesson, `00_meta/patterns/` for a cross-project one. The check runs in the handler before either write mode, so batch mode spends no worker call on lessons it cannot write. `_write_lesson` also refuses on its own, so the invariant does not depend on every caller running the pre-check.
**Follow-up (#436):** The first version checked for the file and then called `open(path, "a")`, and `"a"` carries `O_CREAT`. A deletion between the check and the open still recreated the file, so the check alone did not make the writer refuse. The append now uses `os.open(path, os.O_WRONLY | os.O_APPEND)`, which fails if the file is gone at the moment of the write, and a race test that fails on the old code covers it. Refusing only holds if nothing on the writer's path can create the file, so `O_CREAT` must not appear anywhere on it.
**Why:** **When a migration deletes something on purpose, every writer that could recreate it has to learn the difference, not just the readers.** Readers that miss a migration fail visibly: they return less than expected. Writers fail silently, because recreating what is missing looks like success. List the writers of whatever a migration removes and make each one refuse, not repair. Where a missing target has more than one possible cause, refusing is the only answer that is correct for all of them.
**Tags:** `#migration` `#knowledge-placement` `#silent-failure` `#capture-lesson` `#HIVE-431`
