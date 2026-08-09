"""AC3, separate-processes regime — the measurement behind the rescope.

ADR-018 originally scoped the reconciler to the Phase C daemon, on the
argument that one process means one queue and therefore no cross-process
coordination. It was rescoped to run in *every* hive process because that
coordination already exists and is already load-bearing: ``_git_filelock``
is acquired by every write path, so the queue changes commit *frequency*,
not the concurrency model.

That argument is only as good as its arithmetic. One queue at a 5 s tick is
a ~5% duty cycle on a 25 ms commit; ten process-local queues at the same tick
is the same ~5%, redistributed and serialized by the filelock already
guarding it. This file measures the redistributed case and asserts the
analogous bound — ``P x elapsed/tick``, still independent of write count.

Marked ``cross_worker`` and excluded from the default run: it spawns real
interpreters, and the flake budget of a required merge gate should not be
spent on a benchmark. Its verification command runs it explicitly.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.cross_worker,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX process spawning; the filelock contract itself is covered cross-OS",
    ),
]

# Each worker is a genuinely separate hive process with its own reconciler and
# its own queue, contending for the one `_git_filelock` the rescope rests on.
_WORKER = """
import sys
from pathlib import Path

from hive._commit_queue import CommitReconciler

vault, worker_id, writes, tick_s = (
    Path(sys.argv[1]),
    sys.argv[2],
    int(sys.argv[3]),
    float(sys.argv[4]),
)

reconciler = CommitReconciler(vault, tick_s=tick_s)
target_dir = vault / "10_projects" / "testproject" / "mp"
target_dir.mkdir(parents=True, exist_ok=True)

for i in range(writes):
    target = target_dir / f"{worker_id}-{i}.md"
    # The file reaches disk BEFORE its path is queued. That ordering is what
    # makes an unflushed path a delayed commit rather than lost data.
    target.write_text(f"# {worker_id} {i}\\n", encoding="utf-8")
    reconciler.enqueue(target)

# Let the loop fire a few times under real cross-process contention, rather
# than measuring only the shutdown drain.
import time as _time

_time.sleep(tick_s * 3)
reconciler.close(drain=True)
"""


def _rev_count(vault: Path) -> int:
    return int(
        subprocess.run(  # noqa: S603, S607
            ["git", "rev-list", "--count", "HEAD"],
            cwd=vault,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )


def test_commit_count_is_bounded_per_process_in_the_separate_process_regime(
    git_vault: Path,
    tmp_path: Path,
) -> None:
    """P processes commit at most ``P x elapsed/tick``, not once per write.

    The bound is per-process because each process owns a queue and a tick;
    the filelock serializes their commits but does not merge them. That is
    precisely the cost of the rescope, and the reason a single daemon-owned
    queue remains the best case rather than the precondition.

    Deliberately a *ceiling* and not an equality: a cross-process filelock
    timeout legitimately drops a drained batch (ADR-018 §1, AC14), which can
    only lower the count. ``>= 1`` covers the other direction, so the test
    cannot pass by everything silently failing.
    """
    workers, per_worker, tick_s = 3, 60, 0.5
    total_writes = workers * per_worker

    script = tmp_path / "mp_worker.py"
    script.write_text(_WORKER, encoding="utf-8")

    before = _rev_count(git_vault)
    t0 = time.perf_counter()
    procs = [
        subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                str(script),
                str(git_vault),
                f"w{worker_id}",
                str(per_worker),
                str(tick_s),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for worker_id in range(workers)
    ]
    for proc in procs:
        # Generous by design: a tight join would turn interpreter startup on a
        # loaded machine into a failure of something this test does not measure.
        _out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"worker failed: {err.decode('utf-8', 'replace')}"
    elapsed = time.perf_counter() - t0
    commits = _rev_count(git_vault) - before

    # Interpreter startup inflates `elapsed`, which only loosens the bound.
    bound = workers * (math.ceil(elapsed / tick_s) + 2)

    print(f"\n{'=' * 72}")
    print("HIVE-322 commit rate — separate processes sharing one _git_filelock")
    print(f"{'=' * 72}")
    print(f"  Processes             : {workers}")
    print(f"  Writes                : {total_writes}")
    print(f"  Tick                  : {tick_s:.1f} s")
    print(f"  Elapsed               : {elapsed:.2f} s")
    print(f"  Commits               : {commits}  (bound {bound})")
    print(f"  Writes per commit     : {total_writes / max(commits, 1):.1f}")

    assert bound < total_writes, (
        f"load too small to discriminate: bound {bound} >= {total_writes} writes"
    )
    assert commits >= 1, "no process committed at all — the regime proves nothing"
    assert commits <= bound, (
        f"{commits} commits for {total_writes} writes across {workers} processes "
        f"in {elapsed:.2f}s exceeds P x (elapsed/tick + 2) = {bound}; the commit "
        f"rate is tracking write volume again"
    )
