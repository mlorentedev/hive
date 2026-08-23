"""Guards for the four code findings raised in review of PR 2 (hive#395).

Each was verified against the running code before being fixed, and each is
guarded here so the fix cannot regress silently.

The one worth reading twice is the ambiguous-failure case. It is the only one
that costs money when it goes wrong, and it is the only one whose correct
behaviour is to do *less* than the obvious thing.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hive._delegate import EXIT_OK, EXIT_TASK_FAILED, EXIT_USAGE, _decode, run_delegate
from hive.clients import OpenAICompatibleClient

_KEY = "pk-echoed-back-4b21d0"


class _Body:
    """A tool result carrying an arbitrary text body."""

    def __init__(self, text: str) -> None:
        class _C:
            def __init__(self, t: str) -> None:
                self.text = t

        self.content = [_C(text)]


class TestHelpIsNotAUsageError:
    @pytest.mark.parametrize("flag", ["--help", "-h"])
    def test_asking_for_usage_exits_zero(
        self, flag: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A script asking how to call this was being told it called it wrong.

        `argparse` raises SystemExit(0) for --help through the same branch that
        handles a genuine parse failure, so collapsing both to EXIT_USAGE made
        `hive delegate --help` exit 2.
        """
        assert run_delegate([flag]) == EXIT_OK
        assert "--model" in capsys.readouterr().out

    def test_a_real_parse_failure_still_exits_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert run_delegate(["--model", "m"]) == EXIT_USAGE
        assert capsys.readouterr().out == ""


class TestAnUnusableBodyStillProducesARecord:
    """stdout must stay parseable even when the worker's body is not.

    Before the fix, `[]`, `null` and `"text"` all parsed as valid JSON and then
    raised TypeError on the `degraded` assignment — straight out of the verb, so
    the dispatcher got a traceback on stderr and NOTHING on stdout.
    """

    @pytest.mark.parametrize(
        ("body", "why"),
        [
            ("[]", "a list is not a record"),
            ("null", "null is not a record"),
            ('"text"', "a bare string is not a record"),
            ("3", "a number is not a record"),
            ("{}", "an empty object carries no status"),
            ('{"status": "ok"}', "an object missing the contracted keys"),
        ],
    )
    def test_it_becomes_a_task_failure_rather_than_a_crash(self, body: str, why: str) -> None:
        record = _decode(_Body(body), degraded=True)
        assert record["status"] == "task_failed", why
        assert record["degraded"] is True
        assert record["detail"], "an unusable body with no explanation is unactionable"

    def test_a_complete_record_passes_through_untouched(self) -> None:
        good = {
            "status": "ok",
            "model": "m",
            "output": "hi",
            "tokens": 3,
            "duration_ms": 7,
            "detail": "",
        }
        record = _decode(_Body(json.dumps(good)), degraded=False)
        assert record["status"] == "ok"
        assert record["output"] == "hi"
        assert record["degraded"] is False

    def test_the_verb_emits_it_and_exits_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        """End to end: the contract holds even on the malformed path."""
        with patch(
            "hive._delegate._dispatch_once", return_value=_decode(_Body("[]"), degraded=True)
        ):
            code = run_delegate(["--model", "m", "--timeout", "5", "--prompt", "hi"])
        assert code == EXIT_TASK_FAILED
        json.loads(capsys.readouterr().out)


class TestAnAmbiguousDaemonFailureIsNeverRetried:
    """The finding that costs money when it is wrong.

    The daemon records usage as soon as the worker answers, before serialising
    the response. A connection that dies after the request went out therefore
    leaves an outcome nobody can classify: the inference may have run and been
    billed. Falling back locally there runs a SECOND one — double cost, and a
    second slot out of a pool whose concurrency is the binding constraint.

    So the fallback is pre-submission only. Failing to open the session is
    unambiguous and degrades as documented; failing after `call_tool` was
    invoked returns a task failure that names the ambiguity, and deliberately
    NOT exit 3, which would advance the dispatcher's chain and spend a second
    pool on a task that may already have been served.
    """

    def _local(self) -> AsyncMock:
        return AsyncMock(
            return_value=_Body(
                json.dumps(
                    {
                        "status": "ok",
                        "model": "local",
                        "output": "second inference!",
                        "tokens": 1,
                        "duration_ms": 1,
                        "detail": "",
                    }
                )
            )
        )

    def test_a_failure_after_submission_does_not_run_a_second_inference(self) -> None:
        from hive import _delegate

        class _DiesAfterSubmit:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *a: object) -> None:
                return None

            async def call_tool(self, _n: str, _a: dict[str, Any]) -> Any:
                raise ConnectionResetError("response lost in flight")

        local = self._local()
        with (
            patch("hive._client._read_state", return_value=(4242, "t")),
            patch("hive._client._daemon_reachable", return_value=True),
            patch("hive._client._remote_client", return_value=_DiesAfterSubmit()),
            patch("hive.server.create_server") as make,
        ):
            make.return_value.call_tool = local
            record = _delegate._dispatch_once(
                prompt="x", model="m", timeout_s=5.0, context="", max_tokens=10
            )

        assert local.await_count == 0, "a second inference was dispatched for the same task"
        assert record["status"] == "task_failed"
        assert record["status"] != "pool_unavailable", "exit 3 would advance the chain"
        assert "billed" in record["detail"] or "twice" in record["detail"]

    def test_a_failure_before_submission_still_degrades(self) -> None:
        """The unambiguous half must keep working, or the fix broke the fallback."""
        from hive import _delegate

        class _DiesOnHandshake:
            async def __aenter__(self) -> Any:
                raise ConnectionRefusedError("daemon mid-restart")

            async def __aexit__(self, *a: object) -> None:
                return None

        local = self._local()
        with (
            patch("hive._client._read_state", return_value=(4242, "t")),
            patch("hive._client._daemon_reachable", return_value=True),
            patch("hive._client._remote_client", return_value=_DiesOnHandshake()),
            patch("hive.server.create_server") as make,
        ):
            make.return_value.call_tool = local
            record = _delegate._dispatch_once(
                prompt="x", model="m", timeout_s=5.0, context="", max_tokens=10
            )

        assert local.await_count == 1, "nothing ran remotely, so the fallback should have"
        assert record["degraded"] is True
        assert record["status"] == "ok"


class TestTheCatalogErrorIsRedactedToo:
    """AC7's fix covered the 401 path and left the catalog path raw.

    `worker_status` renders this exception, so a provider echoing the
    Authorization value in a 500 body put the credential on a status surface —
    reachable by a different status code than the one that was fixed.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [500, 400, 503])
    async def test_an_echoed_key_is_redacted_in_a_catalog_error(self, status_code: int) -> None:
        client = OpenAICompatibleClient(
            base_url="https://provider.example/v1", api_key=_KEY, default_model="m"
        )
        echoing = httpx.Response(
            status_code=status_code,
            json={"error": {"message": f"upstream failure for Bearer {_KEY}"}},
            request=httpx.Request("GET", "http://test"),
        )
        with (
            patch.object(client._http, "get", new_callable=AsyncMock, return_value=echoing),
            pytest.raises(RuntimeError) as excinfo,
        ):
            await client.list_models()

        assert _KEY not in str(excinfo.value)
        assert "<redacted>" in str(excinfo.value)
