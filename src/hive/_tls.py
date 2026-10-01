"""Pinned TLS for clients of the stable endpoint (ADR-022 Amendment 1).

The stable port is predictable, so whoever answers on it must prove it holds
the owner's per-user key before any bearer or tool traffic is sent. Clients
trust exactly one certificate: the owner's ``CA:FALSE`` self-signed one. No
system root is loaded, so a certificate from any other issuer, or another
self-signed one, fails the handshake.

Stdlib only. The stdio relay imports this module and must stay within the
HIVE-437 import budget, so ``cryptography`` is never imported here.
"""

from __future__ import annotations

import hashlib
import ssl

LOOPBACK_HOST = "127.0.0.1"


def pinned_context(cert_pem: str) -> ssl.SSLContext:
    """A TLS 1.3 client context whose only trust anchor is *cert_pem*.

    Hostname checking stays on: the certificate's only SAN is
    ``IP:127.0.0.1``, so callers connect with ``server_hostname="127.0.0.1"``.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cadata=cert_pem)
    return context


def pem_fingerprint(cert_pem: str) -> str:
    """Hex SHA-256 of the certificate's DER form, the value clients pin."""
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert_pem)).hexdigest()


def fingerprint_matches(sock: ssl.SSLSocket, expected: str) -> bool:
    """Whether the peer on *sock* presented exactly the pinned certificate."""
    der = sock.getpeercert(binary_form=True)
    return der is not None and hashlib.sha256(der).hexdigest() == expected
