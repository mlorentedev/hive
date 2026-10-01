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
from typing import TYPE_CHECKING

from hive._credential import _verify_owner_only

if TYPE_CHECKING:
    from pathlib import Path

LOOPBACK_HOST = "127.0.0.1"
_PEM_HEADER = "-----BEGIN CERTIFICATE-----"


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


_X509_V_ERR_CERT_HAS_EXPIRED = 10


def presented_expired_pin(exc: BaseException) -> bool:
    """Whether a failed handshake was the pinned certificate, past its validity.

    A foreign certificate fails chain building first (``self-signed
    certificate``), so this code means the listener sent the pinned one. It
    does not prove the listener holds the key: the chain is checked before the
    signature that would. Callers still refuse; they only word the reason.
    """
    return (
        isinstance(exc, ssl.SSLCertVerificationError)
        and exc.verify_code == _X509_V_ERR_CERT_HAS_EXPIRED
    )


def load_pinned_certificate(path: Path) -> str:
    """Read the owner's certificate, failing closed like the token read.

    The file is the trust anchor, so a copy anyone else could write would let
    them choose who the client trusts. It must be owner-only.
    """
    try:
        pem = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"could not read the daemon identity certificate at {path}; "
            "start the daemon once or run `hive service rotate-identity`",
        ) from exc
    if pem.count(_PEM_HEADER) != 1 or "PRIVATE KEY" in pem:
        raise RuntimeError(f"invalid daemon identity certificate at {path}")
    if not _verify_owner_only(path):
        raise RuntimeError(f"daemon identity certificate at {path} is not owner-only")
    return pem
