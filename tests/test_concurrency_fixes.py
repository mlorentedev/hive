"""Regression tests for the multi-process git contention fix.

``_git_commit`` serialized only intra-process (``threading.Lock``).
Multiple hive subprocesses (one per Claude Code session) competed on
the same ``vault/.git/index.lock`` and triggered 30s subprocess
timeouts. Fixed by an additional inter-process ``filelock`` under
``vault/.git/hive.lock``.

The respond-after-cancel crash these tests used to cover alongside it is
guarded by ``tests/test_cancel_race.py`` since #434.
"""

from __future__ import annotations

import multiprocessing as mp
import subprocess
from typing import TYPE_CHECKING

from hive._helpers import _git_commit

if TYPE_CHECKING:
    from pathlib import Path


def _init_git_repo(repo: Path) -> None:
    """Initialize a minimal git repo with an initial commit."""
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@hive.local"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "hive-test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    seed = repo / "README.md"
    seed.write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _commit_worker(vault: str, file_name: str) -> str:
    """Worker process: write a file and commit it via _git_commit."""
    from pathlib import Path as _Path

    vault_path = _Path(vault)
    file_path = vault_path / file_name
    file_path.write_text(f"content for {file_name}\n", encoding="utf-8")
    _git_commit(vault_path, [_Path(file_name)], f"add {file_name}")
    return file_name


def _append_to_shared_file_worker(vault: str, shared_file: str, line: str) -> str:
    """Worker process: append a line to a shared file under the inter-process lock.

    Mimics what ``vault_write`` (operation=append) does end-to-end:
    take the inter-process filelock, read the file, append, write, commit.
    Two processes running this against the same file must not lose lines.
    """
    from pathlib import Path as _Path

    from hive._helpers import _git_filelock

    vault_path = _Path(vault)
    file_path = vault_path / shared_file
    with _git_filelock(vault_path).acquire(timeout=30):
        existing = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        file_path.write_text(existing + line + "\n", encoding="utf-8")
        _git_commit(vault_path, [_Path(shared_file)], f"append: {line}")
    return line


class TestInterProcessGitContention:
    """Concurrent ``_git_commit`` from multiple processes must serialize cleanly."""

    def test_concurrent_processes_complete_within_budget(
        self,
        tmp_path: Path,
    ) -> None:
        """8 concurrent processes committing distinct files must all succeed in <60s.

        Without the inter-process ``filelock``, the second-onwards processes
        would lose the race for ``.git/index.lock``, each waiting up to 30s
        for the ``git add`` subprocess timeout to fire.
        """
        _init_git_repo(tmp_path)

        # mp.Pool spawns N processes; each calls _commit_worker with a unique file.
        # All share the same vault path → race on .git/index.lock.
        file_names = [f"file_{i:02d}.md" for i in range(8)]
        args = [(str(tmp_path), n) for n in file_names]

        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=4) as pool:
            results = pool.starmap_async(_commit_worker, args)
            # 60s budget — generous but well below the unfixed worst case
            # (8 procs × 30s git timeout = 240s upper bound without filelock).
            collected = results.get(timeout=60)

        assert set(collected) == set(file_names)

        # All files should be committed (not just written) — verify via git log.
        log = subprocess.run(
            ["git", "log", "--pretty=format:%s"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        committed_files = {ln.removeprefix("add ").strip() for ln in log if ln.startswith("add ")}
        assert committed_files == set(file_names), (
            f"missing commits: {set(file_names) - committed_files}"
        )

    def test_concurrent_appends_to_same_file_lose_no_lines(
        self,
        tmp_path: Path,
    ) -> None:
        """8 processes appending unique lines to one file must all survive.

        This is the bug that previously made parallel ``capture_lesson``
        calls silently drop lessons: the per-process threading lock
        serialized the read-modify-write within one process, but two
        processes both read the same pre-image, both wrote post-images,
        and the last writer overwrote the first's append.
        """
        _init_git_repo(tmp_path)
        shared = "shared.md"
        (tmp_path / shared).write_text("# header\n", encoding="utf-8")

        lines = [f"line-{i:02d}" for i in range(8)]
        args = [(str(tmp_path), shared, ln) for ln in lines]

        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=4) as pool:
            collected = pool.starmap_async(
                _append_to_shared_file_worker,
                args,
            ).get(timeout=90)

        assert set(collected) == set(lines)

        final = (tmp_path / shared).read_text(encoding="utf-8").splitlines()
        present = [ln for ln in lines if ln in final]
        missing = [ln for ln in lines if ln not in final]
        assert not missing, (
            f"write-loss: {len(missing)} of 8 appends lost.\n"
            f"present={present}\nmissing={missing}\nfinal={final!r}"
        )
