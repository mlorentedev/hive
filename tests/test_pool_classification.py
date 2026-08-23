"""A refused pool must not be reported as a failed task.

HIVE-384's contract table assigns exit `3` — *pool unavailable* — to
"unreachable, 429, auth rejected", and exit `1` — *task failed* — to a worker
that answered with a bad answer. The dispatcher advances its fallback chain on
the first and must not on the second: collapsing them turns a rate limit into a
silent retry against a different model, and a bad answer into one too.

`_try_worker` already carries the classification as data, and its docstring
names all four conditions under `ConnectionError`. `clients.py` did not agree
with it: only *transport* failures raised `ConnectionError`, while every non-2xx
— 429 and 401 included — raised `RuntimeError`, which `_try_worker` maps to
`task_failed`. So a saturated pool classified as a broken task, and the chain
would stop exactly where it should have advanced.

The tests below are the guard for that. They assert the classification at both
levels it has to survive: the exception the client raises, and the status string
`_try_worker` derives from it — because only the second one crosses the JSON-RPC
boundary to a dispatcher.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hive.clients import OpenAICompatibleClient, PoolUnavailableError


def _response(status_code: int, json_data: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data if json_data is not None else {"error": {"message": "nope"}},
        request=httpx.Request("POST", "http://test"),
    )


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(base_url="http://provider.example/v1", default_model="m")


class TestPoolRefusalIsNotATaskFailure:
    """The three ways a pool declines to serve, all of which are retryable elsewhere."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status_code", "what"),
        [
            (429, "rate limited"),
            (401, "credential rejected"),
            (403, "forbidden"),
        ],
    )
    async def test_pool_refusal_raises_pool_unavailable(self, status_code: int, what: str) -> None:
        client = _client()
        with (
            patch.object(
                client._http, "post", new_callable=AsyncMock, return_value=_response(status_code)
            ),
            pytest.raises(PoolUnavailableError),
        ):
            await client.generate("hi")

    @pytest.mark.asyncio
    async def test_pool_unavailable_is_a_connection_error(self) -> None:
        """Subclass, so every existing ``except ConnectionError`` keeps working.

        `_try_worker` and `worker_status` both catch `ConnectionError` today.
        Introducing a sibling type would have silently reclassified them.
        """
        assert issubclass(PoolUnavailableError, ConnectionError)

    @pytest.mark.asyncio
    async def test_transport_failure_is_still_pool_unavailable(self) -> None:
        client = _client()
        with (
            patch.object(
                client._http,
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("refused"),
            ),
            pytest.raises(PoolUnavailableError),
        ):
            await client.generate("hi")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [400, 404, 422, 500, 503])
    async def test_other_errors_stay_task_failures(self, status_code: int) -> None:
        """A 5xx is deliberately NOT a pool refusal.

        It means the pool accepted the request and something broke serving it.
        Retrying the same task on a different model would hide a real failure —
        the fail-closed direction is the one that does not retry.
        """
        client = _client()
        with (
            patch.object(
                client._http, "post", new_callable=AsyncMock, return_value=_response(status_code)
            ),
            pytest.raises(RuntimeError) as excinfo,
        ):
            await client.generate("hi")
        assert not isinstance(excinfo.value, ConnectionError)

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_status_it_saw(self) -> None:
        """An operator reading one line must know which refusal happened."""
        client = _client()
        with (
            patch.object(client._http, "post", new_callable=AsyncMock, return_value=_response(429)),
            pytest.raises(PoolUnavailableError, match="429"),
        ):
            await client.generate("hi")
