"""Stable local daemon endpoint contract (HIVE-437 / ADR-022)."""

from __future__ import annotations

import importlib

import pytest


def _endpoint_module():
    try:
        return importlib.import_module("hive._endpoint")
    except ModuleNotFoundError:
        pytest.fail("hive._endpoint is not implemented")


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ("posix:0", 49482),
        ("posix:501", 53249),
        ("posix:1000", 54282),
        ("windows:S-1-5-18", 59002),
        ("windows:S-1-5-21-123456789-987654321-555555555-1001", 55925),
    ],
)
def test_port_for_identity_matches_v1_vectors(identity: str, expected: int) -> None:
    endpoint = _endpoint_module()

    assert endpoint.port_for_identity(identity) == expected


def test_default_port_is_always_in_the_private_range() -> None:
    endpoint = _endpoint_module()

    for uid in range(10_000):
        port = endpoint.port_for_identity(f"posix:{uid}")
        assert endpoint.PRIVATE_PORT_MIN <= port <= endpoint.PRIVATE_PORT_MAX


def test_configured_port_uses_identity_formula_when_override_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _endpoint_module()
    monkeypatch.delenv(endpoint.DAEMON_PORT_ENV, raising=False)

    assert endpoint.configured_daemon_port(identity="posix:1000") == 54282


def test_configured_port_honors_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _endpoint_module()
    monkeypatch.setenv(endpoint.DAEMON_PORT_ENV, "60412")

    assert endpoint.configured_daemon_port(identity="posix:1000") == 60412


@pytest.mark.parametrize("value", ["0", "-1", "65536", "not-a-port"])
def test_configured_port_rejects_invalid_override(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    endpoint = _endpoint_module()
    monkeypatch.setenv(endpoint.DAEMON_PORT_ENV, value)

    with pytest.raises(ValueError, match="HIVE_DAEMON_PORT"):
        endpoint.configured_daemon_port(identity="posix:1000")


def test_canonical_identity_requires_the_platform_identifier() -> None:
    endpoint = _endpoint_module()

    assert endpoint.canonical_identity(platform="linux", uid=1000) == "posix:1000"
    assert endpoint.canonical_identity(platform="win32", sid="S-1-5-18") == "windows:S-1-5-18"
    with pytest.raises(RuntimeError, match="SID"):
        endpoint.canonical_identity(platform="win32", sid="")
