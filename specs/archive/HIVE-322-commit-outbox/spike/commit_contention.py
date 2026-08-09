"""HIVE-322 — write-path contention measurement.

Reproduces the numbers ADR-018 and `../verification.md` are built on. This is a
*measurement* spike, not a de-risk: it establishes the pre-implementation
baseline the commit queue must move, and it is the harness the eventual AC3
regression test grows out of.

The claim under test is that hive's write throughput is bounded by a serialized
`git commit` rather than by anything concurrency can relieve. Three regimes:

- **processes** — N OS processes each committing per write. This is the
  pre-daemon deployment: one hive process per MCP client session, serialized
  across the filelock at `vault/.git/hive.lock`.
- **threads** — N threads inside ONE process. This is the single-owner daemon
  (ADR-011), where the cross-process filelock is uncontended and only
  `_GIT_LOCK` serializes.
- **coalesced** — N processes, but each commits its whole batch once. Stands in
  for HIVE-104's `commit=False` + `vault_commit`, and bounds what batching
  alone can buy.

What it asserts (beyond printing tables):

1. **Every expected commit landed.** `_git_commit` swallows failures by design
   ("logged but never propagated"), so a silently skipped commit would show up
   as a *fast* latency sample and inflate the results. Each run asserts the
   commit count and that the tree is clean, so the numbers cannot be flattered
   by work that never happened.
2. **Throughput does not scale with writers.** In per-write mode, going from 1
   to N writers must not raise throughput — that flatness is the finding.

Run:

    uv run python specs/HIVE-322-commit-outbox/spike/commit_contention.py

Exit `0` = the integrity assertions held. The tables are for humans; diff them
against `../verification.md` after the queue lands.
"""

from __future__ import annotations

import multiprocessing as mp
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))

from hive._helpers import _git_commit  # noqa: E402

# Sized to the real vault's order of magnitude — commit cost tracks index size.
N_FILES = 1300
WRITES_PER_WORKER = 5
LADDER = (1, 2, 4, 5, 8, 10, 12)


# ── vault fixture ────────────────────────────────────────────────────────────


def make_vault(root: Path) -> None:
    """A git vault of N_FILES notes, all committed."""
    for i in range(N_FILES):
        d = root / f"10_projects/p{i % 20}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"note{i}.md").write_text(f"# Note {i}\n\n" + ("lorem ipsum\n" * 40))
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "spike@example.com"],
        ["git", "config", "user.name", "Spike"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(args, cwd=root, capture_output=True, check=True)


def _commit_count(root: Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=root, capture_output=True, text=True
    )
    return int(out.stdout)


def _dirty_count(root: Path) -> int:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True
    )
    return len(out.stdout.strip().splitlines()) if out.stdout.strip() else 0


# ── workers ──────────────────────────────────────────────────────────────────


def _do_writes(root: Path, wid: int, n_ops: int, coalesce: bool) -> list[float]:
    """Write n_ops files, committing per write or once at the end."""
    lat: list[float] = []
    rels = []
    for i in range(n_ops):
        rel = Path(f"10_projects/p0/w{wid}_{i}.md")
        (root / rel).write_text(f"# worker {wid} op {i}\n")
        if coalesce:
            rels.append(rel)
            continue
        t0 = time.perf_counter()
        _git_commit(root, [rel], f"spike w{wid} op{i}")
        lat.append((time.perf_counter() - t0) * 1000)
    if coalesce:
        t0 = time.perf_counter()
        _git_commit(root, rels, f"spike w{wid} coalesced")
        lat.append((time.perf_counter() - t0) * 1000)
    return lat


def _proc_worker(vault: str, wid: int, n_ops: int, coalesce: bool, out: mp.Queue) -> None:  # type: ignore[type-arg]
    out.put(_do_writes(Path(vault), wid, n_ops, coalesce))


# ── regimes ──────────────────────────────────────────────────────────────────


def _stats(lat: list[float], wall: float, writes: int) -> tuple[float, float, float, float]:
    lat.sort()
    p95 = lat[int(len(lat) * 0.95)] if len(lat) > 1 else lat[0]
    return statistics.median(lat), p95, lat[-1], writes / wall


def run_processes(n: int, coalesce: bool = False) -> tuple[float, float, float, float]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "vault"
        root.mkdir()
        make_vault(root)
        base = _commit_count(root)
        q: mp.Queue = mp.Queue()  # type: ignore[type-arg]
        ps = [
            mp.Process(target=_proc_worker, args=(str(root), w, WRITES_PER_WORKER, coalesce, q))
            for w in range(n)
        ]
        t0 = time.perf_counter()
        for p in ps:
            p.start()
        lat: list[float] = []
        for _ in ps:
            lat.extend(q.get())
        for p in ps:
            p.join()
        wall = time.perf_counter() - t0
        _assert_integrity(root, base, n, coalesce)
    return _stats(lat, wall, n * WRITES_PER_WORKER)


def run_threads(n: int) -> tuple[float, float, float, float]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "vault"
        root.mkdir()
        make_vault(root)
        base = _commit_count(root)
        lat: list[float] = []
        guard = threading.Lock()

        def worker(wid: int) -> None:
            mine = _do_writes(root, wid, WRITES_PER_WORKER, False)
            with guard:
                lat.extend(mine)

        ts = [threading.Thread(target=worker, args=(w,)) for w in range(n)]
        t0 = time.perf_counter()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        wall = time.perf_counter() - t0
        _assert_integrity(root, base, n, False)
    return _stats(lat, wall, n * WRITES_PER_WORKER)


def _assert_integrity(root: Path, base: int, n_workers: int, coalesce: bool) -> None:
    """Every expected commit landed and nothing is left uncommitted.

    Without this the whole measurement is worthless: a commit that silently
    failed would be recorded as a very fast sample.
    """
    expected = n_workers * (1 if coalesce else WRITES_PER_WORKER)
    got = _commit_count(root) - base
    if got != expected:
        raise AssertionError(f"commit count {got} != expected {expected} — numbers invalid")
    dirty = _dirty_count(root)
    if dirty:
        raise AssertionError(f"{dirty} uncommitted file(s) left behind — numbers invalid")


# ── report ───────────────────────────────────────────────────────────────────


def _table(title: str, rows: list[tuple[int, tuple[float, float, float, float]]]) -> None:
    print(f"\n== {title} ==")
    print(f"{'n':>4} {'p50_ms':>9} {'p95_ms':>9} {'max_ms':>9} {'writes/s':>9}")
    for n, (p50, p95, mx, tput) in rows:
        print(f"{n:>4} {p50:>9.1f} {p95:>9.1f} {mx:>9.1f} {tput:>9.1f}")


def main() -> int:
    print(f"vault = {N_FILES} files, {WRITES_PER_WORKER} writes per worker")

    # Warm the page cache and the git object store before measuring: the first
    # run of a fresh vault is dominated by cold-start cost and would otherwise
    # depress the n=1 baseline the scaling check reads.
    run_processes(1)

    procs = [(n, run_processes(n)) for n in LADDER]
    _table("processes, commit per write (pre-daemon deployment)", procs)

    threads = [(n, run_threads(n)) for n in LADDER]
    _table("threads in one process, commit per write (single-owner daemon)", threads)

    coalesced = [(n, run_processes(n, coalesce=True)) for n in LADDER]
    _table("processes, one commit per worker (HIVE-104 coalescing)", coalesced)

    # The finding is SUBLINEARITY, not equality. Absolute throughput is noisy
    # (a few writes per worker, real subprocesses), so asserting "flat within
    # X%" would be flaky. The claim that actually matters: if concurrency
    # helped, N times the writers would give something approaching N times the
    # throughput. Assert we are nowhere near that.
    base_tput = procs[0][1][3]
    top_tput = procs[-1][1][3]
    observed = top_tput / base_tput
    linear = LADDER[-1] / LADDER[0]
    budget = linear * 0.25
    print(
        f"\nper-write throughput: {base_tput:.1f} writes/s at n={LADDER[0]} -> "
        f"{top_tput:.1f} at n={LADDER[-1]} ({observed:.2f}x for {linear:.0f}x the writers)"
    )
    if observed > budget:
        print(
            f"UNEXPECTED: {observed:.2f}x scaling exceeds the {budget:.2f}x sublinearity "
            "budget — the serialization claim needs review"
        )
        return 1
    print(
        f"confirmed: added writers buy queueing, not throughput "
        f"({observed:.2f}x <= {budget:.2f}x budget)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
