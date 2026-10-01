"""Pinned TLS client context for the stable endpoint (HIVE-456, ADR-022 A1)."""

from __future__ import annotations

import socket
import ssl
import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from hive._identity import Identity

HOST = "127.0.0.1"


def _identity(tmp_path: Path, name: str) -> Identity:
    from hive._identity import create_identity

    return create_identity(tmp_path / f"{name}.key", tmp_path / f"{name}.crt")


class _TlsServer:
    """Accept TLS handshakes on loopback with one fixed certificate."""

    def __init__(self, identity: Identity, *, maximum: ssl.TLSVersion | None = None) -> None:
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(identity.cert_path, identity.key_path)
        if maximum is not None:
            self.context.maximum_version = maximum
        self.listener = socket.create_server((HOST, 0))
        # accept() is not interrupted by close() on every OS; poll instead.
        self.listener.settimeout(0.1)
        self._stop = threading.Event()
        self.port = int(self.listener.getsockname()[1])
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                raw, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            raw.settimeout(5)
            try:
                with self.context.wrap_socket(raw, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, ssl.SSLError):
                raw.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.listener.close()


@pytest.fixture
def owner(tmp_path: Path) -> Iterator[tuple[Identity, _TlsServer]]:
    identity = _identity(tmp_path, "owner")
    server = _TlsServer(identity)
    yield identity, server
    server.close()


def _connect(context: ssl.SSLContext, port: int) -> ssl.SSLSocket:
    raw = socket.create_connection((HOST, port), timeout=5)
    try:
        return context.wrap_socket(raw, server_hostname=HOST)
    except BaseException:
        raw.close()
        raise


def test_pinned_context_trusts_only_the_owner_certificate(
    owner: tuple[Identity, _TlsServer],
    tmp_path: Path,
) -> None:
    from hive._tls import fingerprint_matches, pinned_context

    identity, server = owner
    context = pinned_context(identity.cert_path.read_text(encoding="ascii"))

    with _connect(context, server.port) as tls:
        assert tls.version() == "TLSv1.3"
        assert fingerprint_matches(tls, identity.fingerprint)
        assert not fingerprint_matches(tls, "00" * 32)

    impostor = _TlsServer(_identity(tmp_path, "impostor"))
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            _connect(context, impostor.port)
    finally:
        impostor.close()


def test_pinned_context_refuses_tls12(owner: tuple[Identity, _TlsServer], tmp_path: Path) -> None:
    from hive._tls import pinned_context

    identity, _ = owner
    legacy = _TlsServer(identity, maximum=ssl.TLSVersion.TLSv1_2)
    try:
        context = pinned_context(identity.cert_path.read_text(encoding="ascii"))
        with pytest.raises(ssl.SSLError):
            _connect(context, legacy.port)
    finally:
        legacy.close()


def test_pem_fingerprint_matches_the_identity_fingerprint(tmp_path: Path) -> None:
    from hive._tls import pem_fingerprint

    identity = _identity(tmp_path, "owner")
    assert pem_fingerprint(identity.cert_path.read_text(encoding="ascii")) == identity.fingerprint


def test_load_pinned_certificate_refuses_a_certificate_others_can_write(tmp_path: Path) -> None:
    """The certificate is the trust anchor: whoever can replace it picks who is trusted."""
    import os
    import subprocess

    from hive._identity import create_identity
    from hive._tls import load_pinned_certificate

    identity = create_identity(tmp_path / "daemon.key", tmp_path / "daemon.crt")
    assert load_pinned_certificate(identity.cert_path)  # positive control
    if os.name == "nt":
        subprocess.run(
            ["icacls", str(identity.cert_path), "/grant", "*S-1-1-0:(W)"],
            check=True,
            capture_output=True,
        )
    else:
        identity.cert_path.chmod(0o666)
    with pytest.raises(RuntimeError, match="not owner-only"):
        load_pinned_certificate(identity.cert_path)
