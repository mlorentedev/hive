"""Per-user TLS identity material for the stable endpoint (HIVE-456, ADR-022 A1)."""

from __future__ import annotations

import datetime as dt
import ipaddress
from typing import TYPE_CHECKING

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

if TYPE_CHECKING:
    from pathlib import Path

_MAX_VALIDITY = dt.timedelta(days=398)


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "identity.key", tmp_path / "identity.crt"


def test_create_identity_material_properties(tmp_path: Path) -> None:
    from hive._credential import _verify_owner_only
    from hive._identity import create_identity

    key_path, cert_path = _paths(tmp_path)
    identity = create_identity(key_path, cert_path)

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    public_key = cert.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    assert isinstance(public_key.curve, ec.SECP256R1)

    constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical
    assert constraints.value.ca is False

    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    assert san.get_values_for_type(x509.DNSName) == []

    usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(usage) == [ExtendedKeyUsageOID.SERVER_AUTH]

    validity = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert dt.timedelta(0) < validity <= _MAX_VALIDITY

    assert identity.fingerprint == cert.fingerprint(cert.signature_hash_algorithm).hex()
    assert identity.not_after == cert.not_valid_after_utc
    assert _verify_owner_only(key_path)
    assert _verify_owner_only(cert_path)
    assert b"PRIVATE KEY" in key_path.read_bytes()
    assert b"PRIVATE KEY" not in cert_path.read_bytes()


def test_create_identity_is_atomic_and_never_leaves_partial_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._identity as identity

    key_path, cert_path = _paths(tmp_path)
    real_write = identity.write_owner_only_atomic
    calls: list[Path] = []

    def fail_on_certificate(path: Path, data: bytes) -> None:
        calls.append(path)
        if path == cert_path:
            raise RuntimeError("simulated failure while publishing the certificate")
        real_write(path, data)

    monkeypatch.setattr(identity, "write_owner_only_atomic", fail_on_certificate)

    with pytest.raises(RuntimeError, match="simulated failure"):
        identity.create_identity(key_path, cert_path)

    assert calls == [key_path, cert_path]
    assert not key_path.exists()
    assert not cert_path.exists()
    assert [p.name for p in tmp_path.iterdir()] == []


def test_load_or_create_identity_reuses_existing_material(tmp_path: Path) -> None:
    from hive._identity import load_or_create_identity

    key_path, cert_path = _paths(tmp_path)
    state_path = tmp_path / "identity.state"

    first = load_or_create_identity(key_path, cert_path, state_path)
    key_bytes = key_path.read_bytes()
    second = load_or_create_identity(key_path, cert_path, state_path)

    assert first.fingerprint == second.fingerprint
    assert key_path.read_bytes() == key_bytes
    assert state_path.exists()
