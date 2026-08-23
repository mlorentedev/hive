"""`hive delegate` — the wire contract a dispatcher depends on (AC1, AC2).

The consumer of this verb is not a person, it is `dotf agent run` walking a
fallback chain. That makes three things load-bearing rather than cosmetic:

* **stdout is exactly one JSON object.** A dispatcher parses it. Any log line
  that lands there corrupts the parse, so logging goes to stderr without
  exception.
* **`--model` and `--timeout` are required.** Defaulting either would let the
  caller believe it stated a deadline or a model when it did not, and the whole
  point of the routing registry is that the dispatcher — not the backend —
  decides which model runs.
* **the exit code separates the two failure classes.** `3` means the pool did
  not serve the request, and the dispatcher advances its chain; `1` means it
  did serve it and the answer is a failure, and the dispatcher must stop.
  Collapsing them turns a bad answer into a silent retry on another model.

Exit codes, from the HIVE-384 contract table: 0 ok · 1 task failed · 2 usage
error · 3 pool unavailable · 4 timeout.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from hive._delegate import (
    EXIT_OK,
    EXIT_POOL_UNAVAILABLE,
    EXIT_TASK_FAILED,
    EXIT_USAGE,
    run_delegate,
)

_ARGS = ["--model", "a-model", "--timeout", "30", "--prompt", "hi"]


def _result(**over: Any) -> dict[str, Any]:
    base = {
        "status": "ok",
        "model": "a-model",
        "degraded": False,
        "tokens": 12,
        "duration_ms": 40,
        "output": "hello",
        "detail": "",
    }
    base.update(over)
    return base


class TestRequiredArguments:
    """Neither a model nor a deadline may be assumed on the caller's behalf."""

    @pytest.mark.parametrize(
        ("argv", "missing"),
        [
            (["--timeout", "30", "--prompt", "hi"], "--model"),
            (["--model", "m", "--prompt", "hi"], "--timeout"),
            (["--model", "m", "--timeout", "30"], "--prompt"),
        ],
    )
    def test_missing_required_argument_is_a_usage_error(
        self, argv: list[str], missing: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_delegate(argv) == EXIT_USAGE
        captured = capsys.readouterr()
        assert captured.out == "", "a usage error must not emit a result record"
        assert missing in captured.err

    def test_a_non_numeric_timeout_is_a_usage_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_delegate(["--model", "m", "--timeout", "soon", "--prompt", "hi"]) == EXIT_USAGE
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("timeout", ["0", "-5"])
    def test_a_non_positive_timeout_is_a_usage_error(
        self, timeout: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A zero deadline is not a deadline; rejecting beats an instant exit 4."""
        assert run_delegate(["--model", "m", "--timeout", timeout, "--prompt", "hi"]) == EXIT_USAGE
        assert capsys.readouterr().out == ""


class TestWireContract:
    def test_stdout_is_exactly_one_json_object(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("hive._delegate._dispatch_once", return_value=_result()):
            assert run_delegate(_ARGS) == EXIT_OK
        out = capsys.readouterr().out
        assert out.endswith("\n")
        assert len(out.strip().splitlines()) == 1, "a dispatcher parses this as one object"
        json.loads(out)

    def test_the_record_carries_every_contracted_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("hive._delegate._dispatch_once", return_value=_result()):
            run_delegate(_ARGS)
        record = json.loads(capsys.readouterr().out)
        assert set(record) >= {"status", "model", "degraded", "tokens", "duration_ms", "output"}

    def test_the_record_names_the_model_that_answered(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Not the requested one: the dispatcher reports what actually ran."""
        with patch("hive._delegate._dispatch_once", return_value=_result(model="what-ran")):
            run_delegate(_ARGS)
        assert json.loads(capsys.readouterr().out)["model"] == "what-ran"

    def test_logging_never_reaches_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        import logging

        def _noisy(**_: Any) -> dict[str, Any]:
            logging.getLogger("hive").warning("a log line that must not corrupt the parse")
            return _result()

        with patch("hive._delegate._dispatch_once", side_effect=_noisy):
            run_delegate(_ARGS)
        json.loads(capsys.readouterr().out)  # raises if the log landed on stdout


class TestExitCodesSeparateTheFailureClasses:
    """The distinction the whole fallback chain rests on."""

    def test_pool_unavailable_exits_three(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "hive._delegate._dispatch_once",
            return_value=_result(status="pool_unavailable", output="", detail="429 rate limited"),
        ):
            assert run_delegate(_ARGS) == EXIT_POOL_UNAVAILABLE
        assert json.loads(capsys.readouterr().out)["status"] == "pool_unavailable"

    def test_task_failed_exits_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "hive._delegate._dispatch_once",
            return_value=_result(status="task_failed", output="", detail="worker error: 500"),
        ):
            assert run_delegate(_ARGS) == EXIT_TASK_FAILED
        assert json.loads(capsys.readouterr().out)["status"] == "task_failed"

    def test_a_failure_still_emits_a_parseable_record(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The dispatcher needs the record on the failure path most of all."""
        with patch(
            "hive._delegate._dispatch_once",
            return_value=_result(status="pool_unavailable", detail="unreachable"),
        ):
            run_delegate(_ARGS)
        record = json.loads(capsys.readouterr().out)
        assert record["detail"], "a failure with no detail is unactionable"

    def test_an_unrecognised_status_is_a_task_failure(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Fail closed: the direction that does NOT advance to another model.

        A status this verb does not know about must never be read as 'the pool
        was busy', because that is the reading that silently retries.
        """
        with patch("hive._delegate._dispatch_once", return_value=_result(status="something-new")):
            assert run_delegate(_ARGS) == EXIT_TASK_FAILED
