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


# ── Lifecycle after the identity exists (HIVE-456 AC6, PR 2) ─────────────────


def _existing_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    issued: dt.datetime | None = None,
) -> tuple[str, str]:
    """A state dir past its first TLS start: identity, record and token."""
    from hive._daemon import _create_token
    from hive._endpoint import (
        identity_cert_path,
        identity_key_path,
        identity_state_path,
        token_file_path,
    )
    from hive._identity import STATE_RECORD, create_identity
    from hive._owner_only import write_owner_only_atomic

    _state_dir(monkeypatch, tmp_path)
    identity = create_identity(identity_key_path(), identity_cert_path(), now=issued)
    write_owner_only_atomic(identity_state_path(), STATE_RECORD)
    return _create_token(token_file_path()), identity.fingerprint


def _serve_once(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run ``run_serve`` up to the point it would serve; return what it served with."""
    import hive._daemon as daemon

    served: list[str] = []
    monkeypatch.setattr(daemon, "_startup_self_heal", lambda vault: None)
    monkeypatch.setattr(daemon, "_port_available", lambda host, port: True)
    monkeypatch.setattr(daemon, "configured_daemon_port", lambda: 54283)
    monkeypatch.setattr(
        daemon,
        "_serve_owned",
        lambda host, port, token, identity: served.append(identity.fingerprint) or False,
    )
    return served


def test_expired_certificate_is_regenerated_and_token_kept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hive._daemon import prepare_daemon_credentials

    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=500)
    token, old_fingerprint = _existing_identity(monkeypatch, tmp_path, issued=long_ago)

    kept, identity = prepare_daemon_credentials()

    assert kept == token
    assert identity.fingerprint != old_fingerprint
    assert identity.not_after > dt.datetime.now(dt.UTC)


def _make_over_permissive(path: Path) -> None:
    import os
    import subprocess

    if os.name == "nt":
        subprocess.run(
            ["icacls", str(path), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
        )
    else:
        path.chmod(0o644)


def _assert_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    key_body: str,
    token: str,
) -> str:
    import hive._daemon as daemon

    served = _serve_once(monkeypatch)
    assert daemon.run_serve() != 0
    assert served == [], "the daemon served with untrusted identity material"
    out = capsys.readouterr()
    emitted = out.out + out.err
    assert "hive service rotate-identity" in emitted
    assert key_body not in emitted
    assert "PRIVATE KEY" not in emitted
    assert token not in emitted
    return emitted


def _key_line(tmp_path: Path) -> str:
    lines = (tmp_path / "daemon.key").read_text(encoding="ascii").splitlines()
    return max(lines[1:-1], key=len)


def test_over_permissive_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token, _ = _existing_identity(monkeypatch, tmp_path)
    key_body = _key_line(tmp_path)
    _make_over_permissive(tmp_path / "daemon.key")

    emitted = _assert_fails_closed(monkeypatch, capsys, key_body, token)
    assert "not owner-only" in emitted


@pytest.mark.parametrize("damage", ["key-deleted", "cert-deleted", "key-truncated"])
def test_missing_or_corrupt_identity_with_record_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    from hive._owner_only import write_owner_only_atomic

    token, _ = _existing_identity(monkeypatch, tmp_path)
    key_body = _key_line(tmp_path)
    key, cert = tmp_path / "daemon.key", tmp_path / "daemon.crt"
    if damage == "key-deleted":
        key.unlink()
    elif damage == "cert-deleted":
        cert.unlink()
    else:
        pem = key.read_bytes()
        write_owner_only_atomic(key, pem[: len(pem) // 2])

    _assert_fails_closed(monkeypatch, capsys, key_body, token)
    assert (tmp_path / "identity.state").exists(), "a failed start must not erase the record"


def test_mismatched_key_and_certificate_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hive._identity import create_identity
    from hive._owner_only import write_owner_only_atomic

    token, _ = _existing_identity(monkeypatch, tmp_path)
    key_body = _key_line(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    stranger = create_identity(other / "daemon.key", other / "daemon.crt")
    write_owner_only_atomic(tmp_path / "daemon.key", stranger.key_path.read_bytes())

    emitted = _assert_fails_closed(monkeypatch, capsys, key_body, token)
    assert "does not match" in emitted


def test_over_permissive_token_fails_closed_with_a_hint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token, _ = _existing_identity(monkeypatch, tmp_path)
    key_body = _key_line(tmp_path)
    _make_over_permissive(tmp_path / "daemon.token")

    emitted = _assert_fails_closed(monkeypatch, capsys, key_body, token)
    assert "not owner-only" in emitted
