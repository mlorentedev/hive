"""A listener run by another local account cannot pass as the daemon (HIVE-456 AC2).

The stable port is deterministic and any local account can bind it first.
This test runs a real impostor under a second account, named by
``HIVE_CROSSUSER_ACCOUNT`` and reached through passwordless ``sudo -u``
(the CI job creates it). It is selected only with ``-m crossuser``.

The impostor is stdlib-only and runs on ``/usr/bin/python3``, so it does not
depend on reading this checkout's virtualenv. It reports, per connection,
the hex of everything it received; that is the positive control (a ClientHello
arrived) and the proof that nothing else did.

The Windows run is manual: ``docs/runbooks/verify-server-identity-windows.md``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.test_daemon import (  # noqa: F401 - daemon_env is a fixture
    _INITIALIZE,
    HOST,
    _free_port,
    _kill_tree,
    _owner_cert_pem,
    _spawn_daemon,
    _wait_ready,
    daemon_env,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = [
    pytest.mark.crossuser,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="the Windows run is manual: docs/runbooks/verify-server-identity-windows.md",
    ),
]

_IMPOSTOR = r"""
import socket, sys
listener = socket.create_server(("127.0.0.1", int(sys.argv[1])))
print("listening", flush=True)
while True:
    conn, _ = listener.accept()
    conn.settimeout(2)
    received = b""
    try:
        received += conn.recv(65536)
        # Answer like a plaintext HTTP server, as AC1's plaintext impostor does.
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        while chunk := conn.recv(65536):
            received += chunk
            if len(received) > 65536:
                break
    except OSError:
        pass
    conn.close()
    print(received.hex(), flush=True)
"""


def _account() -> str:
    account = os.environ.get("HIVE_CROSSUSER_ACCOUNT", "")
    if not account:
        if os.environ.get("HIVE_EVIDENCE_OPTIONAL") == "1":
            pytest.skip("HIVE_CROSSUSER_ACCOUNT unset and HIVE_EVIDENCE_OPTIONAL=1")
        pytest.fail("missing evidence: HIVE_CROSSUSER_ACCOUNT names no second account")
    return account


class _CrossUserImpostor:
    def __init__(self, account: str, port: int) -> None:
        python = shutil.which("python3", path="/usr/bin") or "/usr/bin/python3"
        self.proc = subprocess.Popen(  # noqa: S603 - fixed argv, test-only account
            ["sudo", "-n", "-u", account, python, "-c", _IMPOSTOR, str(port)],  # noqa: S607
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self.proc.stdout is not None
        ready = self.proc.stdout.readline().strip()
        if ready != "listening":
            self.proc.kill()
            _, err = self.proc.communicate(timeout=5)
            pytest.fail(f"the cross-user impostor did not start: {ready!r} {err.strip()!r}")

    def connections(self) -> list[bytes]:
        """Stop the impostor and return what each connection sent it."""
        # sudo relays SIGTERM to the impostor it started.
        self.proc.terminate()
        out, _ = self.proc.communicate(timeout=10)
        return [bytes.fromhex(line) for line in out.splitlines() if line]


@pytest.fixture
def cross_user(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[dict[str, str], Path, int, str]]:
    """The owner's state after a first daemon start, and a free stable port."""
    account = _account()
    env, state_dir = request.getfixturevalue("daemon_env")
    port = _free_port()
    first = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "the owner's daemon did not start"
        _owner_cert_pem(state_dir)
    finally:
        _kill_tree(first)
    yield env, state_dir, port, account


def test_cross_user_impostor_is_refused_and_named(
    cross_user: tuple[dict[str, str], Path, int, str],
) -> None:
    from hive._client import ClientError, HttpRelay
    from hive._delegate import _probe_daemon

    env, state_dir, port, account = cross_user
    cert_pem = (state_dir / "daemon.crt").read_text(encoding="ascii")
    token = (state_dir / "daemon.token").read_text(encoding="ascii").strip()

    impostor = _CrossUserImpostor(account, port)
    try:
        with pytest.raises(ClientError, match="possible impersonation") as refused:
            HttpRelay(HOST, port, token, cert_pem).forward(_INITIALIZE)
        assert token not in str(refused.value)
        assert _probe_daemon(HOST, port, cert_pem) == "unverified"

        start = subprocess.run(  # noqa: S603 - the daemon under test
            [sys.executable, "-m", "hive.server", "serve"],
            env={**env, "HIVE_DAEMON_PORT": str(port)},
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        received = impostor.connections()

    assert start.returncode != 0
    assert "another account" in start.stderr
    assert account not in start.stderr

    # Positive control: both clients dialled the impostor and opened with a
    # ClientHello; neither the bearer nor a request followed.
    assert len(received) >= 2, received
    for sent in received:
        assert sent[:1] == b"\x16", sent[:16]
        assert token.encode() not in sent
        assert b"Authorization" not in sent


def test_owner_daemon_serves_once_the_impostor_is_gone(
    cross_user: tuple[dict[str, str], Path, int, str],
) -> None:
    from hive._client import HttpRelay
    from hive._delegate import _probe_daemon

    env, state_dir, port, account = cross_user
    _CrossUserImpostor(account, port).connections()

    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "the owner's daemon did not take the freed port"
        cert_pem = (state_dir / "daemon.crt").read_text(encoding="ascii")
        token = (state_dir / "daemon.token").read_text(encoding="ascii").strip()
        frames = HttpRelay(HOST, port, token, cert_pem).forward(_INITIALIZE)
        assert "result" in json.loads(json.dumps(frames[-1]))
        assert _probe_daemon(HOST, port, cert_pem) == "verified"
    finally:
        _kill_tree(daemon)
