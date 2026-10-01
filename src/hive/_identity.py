"""Per-user TLS identity for the stable endpoint (ADR-022 Amendment 1).

The daemon proves who it is with an ECDSA P-256 key and a self-signed
``CA:FALSE`` certificate valid only for ``IP:127.0.0.1``. Clients pin that
certificate, so an impostor bound to the stable port fails the TLS handshake
before any bearer or tool traffic is written.

Only the daemon and the rotation command import this module: it needs
``cryptography``. The stdio relay stays stdlib-only and reads the certificate
through ``hive._tls``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import ipaddress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from hive._credential import _verify_owner_only
from hive._owner_only import write_owner_only_atomic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Below the 398-day ceiling ADR-022 A1 sets, so a host can trust the
# certificate under the same lifetime rules it applies to public ones.
VALIDITY = dt.timedelta(days=397)
_BACKDATE = dt.timedelta(minutes=1)
_LOOPBACK = ipaddress.ip_address("127.0.0.1")
_SUBJECT = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hive-daemon")])
STATE_RECORD = b"hive-identity-v1\n"


class IdentityError(RuntimeError):
    """Identity material is missing, invalid or not owner-only. Never holds key bytes."""


@dataclass(frozen=True)
class Identity:
    key_path: Path
    cert_path: Path
    fingerprint: str
    not_after: dt.datetime


def fingerprint(cert: x509.Certificate) -> str:
    """Hex SHA-256 of the DER certificate, the value clients pin."""
    return cert.fingerprint(hashes.SHA256()).hex()


def _build_certificate(key: ec.EllipticCurvePrivateKey, now: dt.datetime) -> x509.Certificate:
    return (
        x509.CertificateBuilder()
        .subject_name(_SUBJECT)
        .issuer_name(_SUBJECT)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _BACKDATE)
        .not_valid_after(now + VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(_LOOPBACK)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )


def create_identity(
    key_path: Path,
    cert_path: Path,
    *,
    now: dt.datetime | None = None,
) -> Identity:
    """Generate and atomically publish a new key and certificate.

    The key is published first. If the certificate cannot be published the key
    is removed again, so a failure never leaves half an identity behind.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    cert = _build_certificate(key, now or dt.datetime.now(dt.UTC))
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    write_owner_only_atomic(key_path, key_pem)
    try:
        write_owner_only_atomic(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    except BaseException:
        with contextlib.suppress(OSError):
            key_path.unlink()
        raise
    return Identity(key_path, cert_path, fingerprint(cert), cert.not_valid_after_utc)


def load_identity(key_path: Path, cert_path: Path) -> Identity:
    """Load existing material, failing closed on anything unexpected."""
    for path in (key_path, cert_path):
        try:
            owner_only = _verify_owner_only(path)
        except OSError as exc:
            raise IdentityError(f"daemon identity file {path.name} is unreadable") from exc
        if not owner_only:
            raise IdentityError(f"daemon identity file {path.name} is not owner-only")
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        # ``from None``: a parser error must never carry key bytes into a traceback.
        raise IdentityError("daemon identity material is corrupt") from None
    if key.public_key() != cert.public_key():
        raise IdentityError("daemon identity key does not match its certificate")
    return Identity(key_path, cert_path, fingerprint(cert), cert.not_valid_after_utc)


def load_or_create_identity(
    key_path: Path,
    cert_path: Path,
    state_path: Path,
    *,
    before_record: Callable[[], None] | None = None,
) -> Identity:
    """Reuse the published identity, or create it on first use.

    ``state_path`` records that an identity was ever generated. Without it the
    material is created; with it, missing, corrupt or over-permissive material
    is a failure, never a silent regeneration. Only an expired certificate is
    regenerated.

    ``before_record`` runs after the material is published and before the
    record is written, so work that must accompany the first identity (the
    pre-TLS token rotation) is retried by the next start if it is interrupted.
    Writing the record first would leave that work skipped forever.
    """
    if state_path.exists():
        identity = load_identity(key_path, cert_path)
        if identity.not_after > dt.datetime.now(dt.UTC):
            return identity
        # Expiry is not exposure: the material passed every check above, so
        # a new key and certificate replace it and the token is kept.
        return create_identity(key_path, cert_path)
    identity = create_identity(key_path, cert_path)
    if before_record is not None:
        before_record()
    write_owner_only_atomic(state_path, STATE_RECORD)
    return identity
