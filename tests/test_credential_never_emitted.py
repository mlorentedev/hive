"""The worker credential never reaches an output surface (AC7).

This criterion shipped in 4.0.0 with its box ticked and no test behind it. The
gap was found while completing this spec's `features.json`, whose verification
command for AC7 selected zero tests — a recorded proof that never ran is
indistinguishable from one that passes.

It is worth more than a checkbox. The injected secrets doctrine names the
transcript as a durable artifact that no scanner reaches and nothing can
un-print, and `worker_status` is the surface most likely to leak here: it exists
to report configuration, and "configured" is one careless line away from
"configured with this value".

Verification is by absence with a planted value, which is the only way to test
this without printing the thing under test.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hive.clients import OpenAICompatibleClient, PoolUnavailableError
from hive.config import HiveSettings
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

    from hive.budget import BudgetTracker

# Distinctive enough that a substring match cannot collide with real output.
_PLANTED = "pk-planted-9f3c1a7e-never-print-me"


@pytest.fixture
def worker_with_planted_key(
    mock_vault: Path,
    budget: BudgetTracker,
    worker_client: OpenAICompatibleClient,
) -> FastMCP:
    worker_client._api_key = _PLANTED
    worker_client._http.headers["Authorization"] = f"Bearer {_PLANTED}"
    return create_server(
        vault_path=mock_vault,
        budget_tracker=budget,
        worker_client=worker_client,
    )


def _text(result: Any) -> str:
    return str(result.content[0].text)


class TestTheCredentialStaysOutOfOutput:
    def test_settings_do_not_expose_it_through_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`repr(settings)` lands in tracebacks and debug logs unbidden."""
        monkeypatch.setenv("HIVE_WORKER_BASE_URL", "https://provider.example/v1")
        monkeypatch.setenv("HIVE_WORKER_API_KEY", _PLANTED)
        settings = HiveSettings()
        assert settings.worker_api_key == _PLANTED, "the fixture must actually plant it"
        assert _PLANTED not in repr(settings)
        assert _PLANTED not in str(settings)

    @pytest.mark.asyncio
    async def test_worker_status_never_prints_it(
        self, worker_with_planted_key: FastMCP, worker_client: OpenAICompatibleClient
    ) -> None:
        """The surface whose whole job is reporting configuration."""
        worker_client.list_models = AsyncMock(return_value=[])  # type: ignore[method-assign]
        out = _text(await worker_with_planted_key.call_tool("worker_status", {}))
        assert _PLANTED not in out

    @pytest.mark.asyncio
    async def test_an_unreachable_worker_does_not_leak_it_in_the_error(
        self, worker_with_planted_key: FastMCP, worker_client: OpenAICompatibleClient
    ) -> None:
        """Failure paths format more state than success paths, so they leak more."""
        worker_client.list_models = AsyncMock(  # type: ignore[method-assign]
            side_effect=PoolUnavailableError("provider.example refused the request (401)"),
        )
        out = _text(await worker_with_planted_key.call_tool("worker_status", {}))
        assert _PLANTED not in out


class TestAProviderThatEchoesTheKeyBackDoesNotGetItRelayed:
    """The one real path from a configured credential to an emitted string.

    Some providers put the request's ``Authorization`` header into the body of
    a 401. `_error_detail` reads that body and hive then places it in an
    exception message, a log line and the result record a dispatcher parses.
    Nothing about that chain is hypothetical, and it lands on the *auth-failure*
    response — the one call guaranteed to happen when a key is wrong.

    This is deliberately narrower than "hive redacts any secret from any
    string". A general scrubber over arbitrary text is a different and much
    larger thing; AC7 asks that a credential this process was given does not
    come back out, and the redaction is exact — it removes this client's own
    key, so it can neither over-redact nor miss a token shaped unexpectedly.
    """

    @pytest.mark.asyncio
    async def test_the_error_detail_redacts_an_echoed_key(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://provider.example/v1",
            api_key=_PLANTED,
            default_model="m",
        )
        echoing_401 = httpx.Response(
            status_code=401,
            json={"error": {"message": f"invalid credentials: Bearer {_PLANTED}"}},
            request=httpx.Request("POST", "http://test"),
        )
        with (
            patch.object(client._http, "post", new_callable=AsyncMock, return_value=echoing_401),
            pytest.raises(PoolUnavailableError) as excinfo,
        ):
            await client.generate("hi")

        message = str(excinfo.value)
        assert _PLANTED not in message, "the provider's echo of our own key was relayed verbatim"
        assert "<redacted>" in message
        assert "401" in message, "redaction must not cost the reader the reason"

    @pytest.mark.asyncio
    async def test_an_unrelated_body_is_left_intact(self) -> None:
        """Redaction is exact, so a body with no key in it is unchanged."""
        client = OpenAICompatibleClient(
            base_url="https://provider.example/v1",
            api_key=_PLANTED,
            default_model="m",
        )
        plain_401 = httpx.Response(
            status_code=401,
            json={"error": {"message": "no organization on this account"}},
            request=httpx.Request("POST", "http://test"),
        )
        with (
            patch.object(client._http, "post", new_callable=AsyncMock, return_value=plain_401),
            pytest.raises(PoolUnavailableError, match="no organization on this account"),
        ):
            await client.generate("hi")

    @pytest.mark.asyncio
    async def test_the_redacted_detail_survives_into_the_result_record(
        self,
        worker_with_planted_key: FastMCP,
        worker_client: OpenAICompatibleClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """End of the chain: what a dispatcher actually parses off stdout."""
        worker_client.generate = AsyncMock(  # type: ignore[method-assign]
            side_effect=PoolUnavailableError(
                "provider.example refused the request (401 credential rejected): "
                "invalid credentials: Bearer <redacted>"
            ),
        )
        with caplog.at_level(logging.DEBUG):
            out = _text(
                await worker_with_planted_key.call_tool(
                    "delegate_task",
                    {"prompt": "x", "structured": True, "timeout_s": 5.0},
                )
            )
        assert _PLANTED not in out
        assert not [r for r in caplog.records if _PLANTED in r.getMessage()]


# ── Daemon bearer and TLS identity (HIVE-456 AC9, ADR-022 A1 check 9) ──────


def _key_body(key_path: Path) -> str:
    """A base64 line from inside the PEM key: present only if key bytes leaked."""
    lines = key_path.read_text(encoding="ascii").splitlines()
    return max(lines[1:-1], key=len)


class TestDaemonSecretsStayOutOfOutput:
    def test_first_tls_start_logs_neither_token_nor_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hive._daemon import _create_token, prepare_daemon_credentials

        monkeypatch.setenv("HIVE_DB_PATH", str(tmp_path / "worker.db"))
        pre_tls = _create_token(tmp_path / "daemon.token")
        with caplog.at_level(logging.DEBUG):
            token, identity = prepare_daemon_credentials()
        out = capsys.readouterr()
        emitted = out.out + out.err + caplog.text
        assert token and pre_tls != token, "the fixture must actually rotate"
        for secret in (token, pre_tls, _key_body(identity.key_path), "PRIVATE KEY"):
            assert secret not in emitted

    def test_corrupt_key_error_carries_no_key_bytes(self, tmp_path: Path) -> None:
        import traceback

        from hive._identity import IdentityError, create_identity, load_identity

        identity = create_identity(tmp_path / "daemon.key", tmp_path / "daemon.crt")
        planted = _key_body(identity.key_path)
        identity.key_path.write_text(
            f"-----BEGIN PRIVATE KEY-----\n{planted}\n-----END PRIVATE KEY-----\n",
            encoding="ascii",
        )
        with pytest.raises(IdentityError) as excinfo:
            load_identity(identity.key_path, identity.cert_path)
        rendered = "".join(traceback.format_exception(excinfo.value))
        assert planted not in rendered
        assert "PRIVATE KEY" not in rendered

    def test_relay_impersonation_report_omits_the_token(self, tmp_path: Path) -> None:
        import os
        import shutil
        import subprocess
        import sys

        from hive._daemon import _create_token, _enforce_owner_only
        from hive._identity import create_identity
        from tests.impostor import Impostor

        owner = create_identity(tmp_path / "owner.key", tmp_path / "owner.crt")
        shutil.copyfile(owner.cert_path, tmp_path / "daemon.crt")
        _enforce_owner_only(tmp_path / "daemon.crt")
        token = _create_token(tmp_path / "daemon.token")
        impostor = Impostor(create_identity(tmp_path / "imp.key", tmp_path / "imp.crt"))
        env = {
            **os.environ,
            "HIVE_DB_PATH": str(tmp_path / "worker.db"),
            "HIVE_DAEMON_PORT": str(impostor.port),
        }
        result = subprocess.run(
            [sys.executable, "-m", "hive.cli", "client"],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        impostor.wait()

        assert result.returncode != 0
        assert "possible impersonation" in result.stderr
        impostor.assert_refused_before_any_request(token)
        assert token not in result.stdout + result.stderr

    def test_delegate_impersonation_logs_omit_the_token(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import json

        from hive import _delegate
        from hive._identity import create_identity
        from tests.impostor import Impostor

        owner = create_identity(tmp_path / "owner.key", tmp_path / "owner.crt")
        impostor = Impostor(None)
        token = "synthetic-bearer-" + "z" * 32
        state = (impostor.port, token, owner.cert_path.read_text(encoding="ascii"))
        with (
            patch("hive._delegate._read_state", return_value=state),
            caplog.at_level(logging.DEBUG),
        ):
            record = _delegate._dispatch_once(
                prompt="x", model="m", timeout_s=5.0, context="", max_tokens=10
            )
        impostor.wait()

        assert "possible impersonation" in record["detail"]
        assert token not in json.dumps(record) + caplog.text
