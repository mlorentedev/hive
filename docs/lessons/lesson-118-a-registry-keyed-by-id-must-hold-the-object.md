---
id: "lesson-118-a-registry-keyed-by-id-must-hold-the-object"
type: lesson
status: active
title: "A registry keyed by `id()` must hold the object it keys"
created: "2026-11-03"
owner: manu
tags: [hive, lesson, python, threading, bounded-call]
---

# A registry keyed by `id()` must hold the object it keys

**Context:** `_bounded_sync_call` gained a breaker (#559). While a timed-out call
on a tracker is still running, later calls on the same tracker are skipped. The
abandoned threads are kept in a module dict keyed by `id(breaker)`, so a breaker
can be any object, including one that does not support weak references.

**Problem:** The first version stored only the thread. A test that passed
`breaker=object()` inline showed the gap: once the first key was collected,
CPython reused its id for the second `object()`, and an unrelated breaker found
the stuck thread and skipped its call. In production the tracker usually stays
alive through the closure the thread runs, but nothing guaranteed that.

**Solution:** The entry stores `(breaker, thread)`. The strong reference keeps the
id reserved for as long as the entry exists, and the entry is removed on the next
call once the thread has exited. Whenever a dict is keyed by `id(obj)`, keep `obj`
alive in the value, or use a `WeakKeyDictionary` and accept that the key must
support weak references.
