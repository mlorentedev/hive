---
id: lesson-104-a-platform-test-that-ci-never-runs-has-never-passed
type: lesson
status: active
created: "2026-10-10"
owner: manu
tags: [hive, lesson, windows, acl, ci, HIVE-484]
---

# A platform test that CI never runs has never passed

**Context:** Every daemon secret goes through `write_owner_only_atomic`: write a
candidate, make it owner-only, verify, publish. On Windows, "owner-only" was an
`icacls /inheritance:r /grant:r <sid>:(F)` edit, and the verify demands an
owner equal to the user and exactly one ACE, for the user.

**Problem:** On an elevated Windows host the daemon never started. Every
candidate failed with "daemon.key candidate is not owner-only", and the
suggested `rotate-identity` goes through the same write, so nothing recovered.
dotfiles#2255 found it when its doctor asked whether the daemon answers on a
GitHub runner (#484). The Windows ACL tests already existed and would have
failed on day one, but they are `skipif(os.name != "nt")`, and the `check` job
runs only on Ubuntu. They had never run anywhere.

**Solution:**

- **Set the whole descriptor at once.** `SetNamedSecurityInfoW` writes a
  protected DACL with one ACE for the user, the Windows `chmod 0o600`, instead
  of editing whatever DACL the file arrived with. That edit had two gaps. An
  elevated token creates files owned by `BUILTIN\Administrators`, which icacls
  leaves alone. And `/inheritance:r` drops only inherited ACEs, so the explicit
  SYSTEM and Administrators ACEs a 0o700 directory hands its files survived.
- **Take ownership only when it is not already the user's.** A standard user
  already owns its files and can always rewrite its own DACL. So the non-admin
  path, which CI cannot reproduce, needs no new right.
- **Run the ACL tests on Windows.** The new `windows_owner_only` job runs every
  test file that exercises the Windows ACL path. It was pushed red before the
  fix, to show it catches the bug.

**Why:** A test skipped on every platform CI uses reads as coverage and is
none. When a test is platform-gated, check that some job runs it on that
platform. The first diagnosis, "the owner is wrong", was half the cause. Only
the printed descriptor (`O:LAG:...D:PAI(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;LA)`)
showed the extra explicit ACEs. Print the state the verify reads before
guessing at a second fix.
