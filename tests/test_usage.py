"""Tests for UsageTracker — vault tool call logging."""

from __future__ import annotations

import threading

from hive.usage import UsageTracker


class TestLogAndStats:
    def test_log_single_call(self) -> None:
        t = UsageTracker()
        t.log_call("vault_query", "hive", 42)
        stats = t.stats()
        assert stats["total_calls"] == 1
        assert stats["total_response_lines"] == 42

    def test_log_multiple_calls(self) -> None:
        t = UsageTracker()
        t.log_call("vault_query", "hive", 10)
        t.log_call("vault_search", "", 20)
        t.log_call("vault_query", "hive", 30)
        stats = t.stats()
        assert stats["total_calls"] == 3
        assert stats["total_response_lines"] == 60

    def test_by_tool_breakdown(self) -> None:
        t = UsageTracker()
        t.log_call("vault_query", "hive", 10)
        t.log_call("vault_query", "hive", 20)
        t.log_call("vault_search", "", 5)
        stats = t.stats()
        assert stats["by_tool"]["vault_query"] == 2
        assert stats["by_tool"]["vault_search"] == 1

    def test_by_project_breakdown(self) -> None:
        t = UsageTracker()
        t.log_call("vault_query", "hive", 10)
        t.log_call("vault_query", "dotfiles", 20)
        t.log_call("vault_query", "hive", 30)
        stats = t.stats()
        assert stats["by_project"]["hive"] == 2
        assert stats["by_project"]["dotfiles"] == 1

    def test_empty_project_excluded_from_by_project(self) -> None:
        t = UsageTracker()
        t.log_call("vault_search", "", 10)
        stats = t.stats()
        assert stats["by_project"] == {}
        assert stats["by_tool"]["vault_search"] == 1

    def test_empty_stats(self) -> None:
        t = UsageTracker()
        stats = t.stats()
        assert stats["total_calls"] == 0
        assert stats["total_response_lines"] == 0
        assert stats["by_tool"] == {}
        assert stats["by_project"] == {}


class TestFileDB:
    def test_creates_parent_dirs(self, tmp_path: object) -> None:
        from pathlib import Path

        db = Path(str(tmp_path)) / "sub" / "usage.db"
        t = UsageTracker(str(db))
        t.log_call("vault_query", "hive", 5)
        assert db.exists()
        assert t.stats()["total_calls"] == 1


class TestThreadSafety:
    """Concurrent access must not raise SQLITE_MISUSE."""

    def test_concurrent_log_call(self, tmp_path: object) -> None:
        from pathlib import Path

        db = Path(str(tmp_path)) / "concurrent.db"
        t = UsageTracker(str(db))
        errors: list[Exception] = []
        n_threads = 8
        n_ops = 50

        def worker(thread_id: int) -> None:
            try:
                for i in range(n_ops):
                    t.log_call(f"tool-{thread_id}", f"project-{i}", 10)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(n_threads)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Thread errors: {errors}"
        assert t.stats()["total_calls"] == n_threads * n_ops

    def test_concurrent_log_and_stats(self, tmp_path: object) -> None:
        from pathlib import Path

        db = Path(str(tmp_path)) / "mixed.db"
        t = UsageTracker(str(db))
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for _ in range(50):
                    t.log_call("vault_query", "hive", 10)
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(50):
                    t.stats(since_days=30)
            except Exception as exc:
                errors.append(exc)

        threads = [
            *[threading.Thread(target=writer) for _ in range(4)],
            *[threading.Thread(target=reader) for _ in range(4)],
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Thread errors: {errors}"
