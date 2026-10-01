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


def _state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HIVE_DB_PATH", str(tmp_path / "worker.db"))
    return tmp_path


def test_first_tls_start_rotates_a_pre_tls_token_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hive._daemon import prepare_daemon_credentials
    from hive._endpoint import identity_state_path, token_file_path

    _state_dir(monkeypatch, tmp_path)
    # A #453 state directory: a token served over plaintext, no identity yet.
    pre_tls = "synthetic-pre-tls-token"
    token_file_path().write_text(pre_tls, encoding="ascii")

    token, identity = prepare_daemon_credentials()

    assert token != pre_tls
    assert token_file_path().read_text(encoding="ascii") == token
    assert identity.key_path.exists()
    assert identity.cert_path.exists()
    assert identity_state_path().exists()

    again, same = prepare_daemon_credentials()
    assert again == token
    assert same.fingerprint == identity.fingerprint


def test_interrupted_first_tls_start_rotates_the_pre_tls_token_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A crash between identity creation and token rotation must not leave the
    pre-TLS token live behind a state record."""
    import hive._daemon as daemon
    from hive._endpoint import identity_state_path, token_file_path

    _state_dir(monkeypatch, tmp_path)
    pre_tls = "synthetic-pre-tls-token"
    token_file_path().write_text(pre_tls, encoding="ascii")

    real_create = daemon._create_token

    def crash(_path: Path) -> str:
        raise RuntimeError("simulated crash before the token rotation")

    monkeypatch.setattr(daemon, "_create_token", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        daemon.prepare_daemon_credentials()
    assert not identity_state_path().exists()
    assert token_file_path().read_text(encoding="ascii") == pre_tls

    monkeypatch.setattr(daemon, "_create_token", real_create)
    token, _ = daemon.prepare_daemon_credentials()
    assert token != pre_tls
    assert identity_state_path().exists()
