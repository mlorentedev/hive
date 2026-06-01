"""Unit tests for the `hive client` shim internals (HIVE-118 / ADR-011).

These run in-process (no subprocess) so they exercise — and attribute coverage
to — the decision logic that the black-box integration tests in
`test_daemon.py` drive end to end: state-file parsing edge cases and the TCP
liveness probe that separates a live daemon from stale state behind a dead port.
"""

from __future__ import annotations

import contextlib
import socket
from typing import TYPE_CHECKING

from hive._client import _daemon_reachable, _read_state

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

HOST = "127.0.0.1"


def _point_state_at(monkeypatch: pytest.MonkeyPatch, state_dir: Path) -> None:
    """Redirect the shim's state-file lookups at *state_dir*."""
    monkeypatch.setattr(
        "hive._client.port_file_path", lambda: state_dir / "daemon.port",
    )
    monkeypatch.setattr(
        "hive._client.token_file_path", lambda: state_dir / "daemon.token",
    )


@contextlib.contextmanager
def _dead_port() -> object:
    """Yield a port nobody listens on, held bound so connects are refused.

    A socket bound without ``listen()`` has no accept queue, so the kernel
    answers a connect with RST (``ECONNREFUSED``) deterministically — and
    holding it open stops another process from grabbing the port mid-test.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


# ── _read_state ──────────────────────────────────────────────────────────


def test_read_state_returns_none_when_files_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _point_state_at(monkeypatch, tmp_path)
    assert _read_state() is None


def test_read_state_parses_valid_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _point_state_at(monkeypatch, tmp_path)
    (tmp_path / "daemon.port").write_text("54321\n", encoding="utf-8")
    (tmp_path / "daemon.token").write_text("  tok-abc  \n", encoding="utf-8")
    assert _read_state() == (54321, "tok-abc")


def test_read_state_rejects_garbage_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _point_state_at(monkeypatch, tmp_path)
    (tmp_path / "daemon.port").write_text("not-a-number", encoding="utf-8")
    (tmp_path / "daemon.token").write_text("tok", encoding="utf-8")
    assert _read_state() is None


def test_read_state_rejects_nonpositive_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _point_state_at(monkeypatch, tmp_path)
    (tmp_path / "daemon.port").write_text("0", encoding="utf-8")
    (tmp_path / "daemon.token").write_text("tok", encoding="utf-8")
    assert _read_state() is None


def test_read_state_rejects_empty_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _point_state_at(monkeypatch, tmp_path)
    (tmp_path / "daemon.port").write_text("12345", encoding="utf-8")
    (tmp_path / "daemon.token").write_text("   ", encoding="utf-8")
    assert _read_state() is None


# ── _daemon_reachable ──────────────────────────────────────────────────────


def test_daemon_reachable_true_when_listening() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    sock.listen(1)
    try:
        assert _daemon_reachable(HOST, sock.getsockname()[1]) is True
    finally:
        sock.close()


def test_daemon_reachable_false_when_refused() -> None:
    with _dead_port() as port:
        assert _daemon_reachable(HOST, port) is False
