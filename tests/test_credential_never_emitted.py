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
