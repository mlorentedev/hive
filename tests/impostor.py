"""Listeners that are not the daemon, for identity tests (HIVE-456, ADR-022 A1).

An impostor records every byte a client sends it. Tests assert two things from
that record: the client really dialled it and opened with a TLS ClientHello
(the positive control), and nothing else followed (no bearer, no request).
"""

from __future__ import annotations

import socket
import ssl
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hive._identity import Identity

TLS_HANDSHAKE_RECORD = b"\x16"


def server_context(identity: Identity) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(identity.cert_path, identity.key_path)
    return context


class Impostor:
    """A listener on the stable port that is not the daemon, recording every byte.

    ``identity=None`` is a plaintext HTTP listener. Otherwise it terminates TLS
    with a certificate the client never pinned and records what it decrypts.
    """

    def __init__(self, identity: Identity | None) -> None:
        self._context = None if identity is None else server_context(identity)
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = int(self.listener.getsockname()[1])
        self.accepted = threading.Event()
        self.raw = bytearray()
        self.decrypted = bytearray()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self.listener.settimeout(10)
        try:
            conn, _ = self.listener.accept()
        except OSError:
            return
        self.accepted.set()
        conn.settimeout(2)
        with conn:
            if self._context is None:
                self._capture_plaintext(conn)
            else:
                self._capture_tls(conn)

    def _capture_plaintext(self, conn: socket.socket) -> None:
        try:
            self.raw += conn.recv(65536)
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            while chunk := conn.recv(65536):
                self.raw += chunk
        except OSError:
            return

    def _capture_tls(self, conn: socket.socket) -> None:
        assert self._context is not None
        try:
            self.raw += conn.recv(5, socket.MSG_PEEK)
            with self._context.wrap_socket(conn, server_side=True) as tls:
                while chunk := tls.recv(65536):
                    self.decrypted += chunk
        except OSError:
            return

    def wait(self) -> None:
        self._thread.join(timeout=10)
        self.listener.close()

    def assert_refused_before_any_request(self, token: str) -> None:
        """The client dialled, sent a ClientHello, and then nothing else."""
        assert self.accepted.is_set(), "the client never dialled the impostor"
        assert self.raw[:1] == TLS_HANDSHAKE_RECORD, bytes(self.raw[:16])
        captured = bytes(self.raw + self.decrypted)
        assert b"Authorization" not in captured
        assert b"POST" not in captured
        assert token.encode() not in captured
        assert self.decrypted == b""


class SilentListener:
    """Accepts the TCP connection, then resets or stalls instead of speaking TLS."""

    def __init__(self, *, reset: bool) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = int(self.listener.getsockname()[1])
        self.accepted = threading.Event()
        self._reset = reset
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        import struct

        self.listener.settimeout(10)
        try:
            conn, _ = self.listener.accept()
        except OSError:
            return
        self.accepted.set()
        if self._reset:
            # SO_LINGER 0 makes close() send an RST.
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            conn.close()
            return
        conn.settimeout(5)
        try:
            conn.recv(65536)
            conn.recv(65536)
        except OSError:
            pass
        conn.close()

    def wait(self) -> None:
        self._thread.join(timeout=10)
        self.listener.close()
