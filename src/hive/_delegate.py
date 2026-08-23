"""``hive delegate`` — the dispatch verb (HIVE-384 AC1–AC4).

The consumer is a dispatcher, not a person: `dotf agent run` walks a fallback
chain declared in `harness/model-map.json` and needs three things from every
attempt — a machine-readable record, a model it can attribute the answer to,
and an exit code that says whether to try the next entry.

**Why this lives behind the daemon.** ADR-011 makes the daemon the sole owner of
``worker.db``, where usage accounting lives. A verb that span up its own server
would be a second writer to that database — the contention class the daemon
model exists to eliminate. ADR-011 §3's fallback applies unchanged: with no
reachable daemon the verb degrades to the in-process stdio path and says so in
its output rather than failing or pretending.

**Why single-shot.** Choosing among pools is the dispatcher's job. A fallback
list here would be a second routing authority, and it would make "which model
answered" unreportable — the drift the routing registry exists to prevent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

_log = logging.getLogger("hive.delegate")

# Exit codes, from HIVE-384's contract table. These are a CROSS-REPO contract:
# `dotf agent run` advances its chain on EXIT_POOL_UNAVAILABLE and must not on
# EXIT_TASK_FAILED. Changing one unilaterally turns a bad answer into a silent
# retry against a different model.
EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_USAGE = 2
EXIT_POOL_UNAVAILABLE = 3
EXIT_TIMEOUT = 4

# Only these statuses advance the chain. Anything else — including a status a
# future version introduces and this one does not know — maps to task-failed,
# because that is the direction that does NOT retry. Fail-closed here means
# "do not silently spend another pool's quota on an outcome we cannot classify".
_STATUS_EXIT = {
    "ok": EXIT_OK,
    "pool_unavailable": EXIT_POOL_UNAVAILABLE,
    "task_failed": EXIT_TASK_FAILED,
    "timeout": EXIT_TIMEOUT,
}

# What a worker body must carry before this verb will pass it on. `degraded` is
# absent deliberately: only the verb knows which route answered, so it is added
# here rather than expected from the far side.
_REQUIRED_FIELDS = frozenset({"status", "model", "output", "tokens", "duration_ms", "detail"})

_DESCRIPTION = """\
Run one task against the configured worker and print a JSON result record.

Single-shot by design: this makes exactly one attempt against exactly one
model. Walking a fallback chain is the caller's job, which is why --model is
required and why the exit code distinguishes a pool that would not serve the
request (3, try the next entry) from a worker that answered with a failure
(1, do not).
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hive delegate",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Required on purpose. A defaulted model would let a dispatcher believe it
    # chose one, and a defaulted timeout would let it believe it set a deadline.
    p.add_argument("--model", required=True, help="concrete model id to run (no aliases)")
    p.add_argument(
        "--timeout",
        required=True,
        type=float,
        help="observable deadline in seconds; overrides the ambient tool timeout",
    )
    p.add_argument("--prompt", required=True, help="the task text")
    p.add_argument("--context", default="", help="optional system context")
    p.add_argument("--max-tokens", type=int, default=2000, help="cap on the response length")
    return p


def _emit(record: dict[str, Any]) -> None:
    """Write the result record to stdout as one line of JSON, and only that.

    Everything else this process says goes to stderr. A dispatcher parses
    stdout, so a stray log line there is not noise — it is a parse error.
    """
    json.dump(record, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stdout.flush()


def run_delegate(argv: list[str]) -> int:
    """Parse ``argv``, dispatch once, emit the record, return the exit code."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse has already explained itself on stderr. Return rather than
        # propagate so neither path reaches the emit path: a record for a call
        # that was never made would be a lie a dispatcher would happily parse.
        #
        # `--help` and `--version` raise SystemExit(0) through this same branch,
        # so the code has to be read rather than assumed: collapsing everything
        # to EXIT_USAGE made `hive delegate --help` exit 2, which is a script
        # asking for usage and being told it got the usage wrong.
        return EXIT_OK if exc.code == 0 else EXIT_USAGE

    # argparse's own type check accepts 0 and negatives. A zero deadline is not
    # a deadline, and rejecting it beats dispatching something that can only
    # exit 4 immediately.
    if args.timeout <= 0:
        print(
            f"hive delegate: --timeout must be greater than 0 (got {args.timeout})",
            file=sys.stderr,
        )
        return EXIT_USAGE

    record = _dispatch_once(
        prompt=args.prompt,
        model=args.model,
        timeout_s=args.timeout,
        context=args.context,
        max_tokens=args.max_tokens,
    )
    _emit(record)
    status = str(record.get("status", ""))
    if status not in _STATUS_EXIT:
        _log.warning("unrecognised worker status %r — classifying as task failed", status)
    return _STATUS_EXIT.get(status, EXIT_TASK_FAILED)


def _dispatch_once(
    *,
    prompt: str,
    model: str,
    timeout_s: float,
    context: str,
    max_tokens: int,
) -> dict[str, Any]:
    """One attempt against one model; returns the record, never raises.

    Routing is decided here and reported as ``degraded``: false when the call
    went through the daemon, true when it fell back to the in-process stdio
    path. The flag is explicit rather than inferred from an absent field,
    because the fallback path has different concurrency and latency behaviour
    and a consumer must be able to tell them apart without parsing prose.
    """
    return asyncio.run(
        _dispatch_async(
            prompt=prompt,
            model=model,
            timeout_s=timeout_s,
            context=context,
            max_tokens=max_tokens,
        )
    )


async def _dispatch_async(
    *,
    prompt: str,
    model: str,
    timeout_s: float,
    context: str,
    max_tokens: int,
) -> dict[str, Any]:
    # DEFAULT_HOST from its defining module, not re-exported through the shim:
    # mypy --strict rejects the indirect import, and one origin is one fact.
    from hive._client import _daemon_reachable, _read_state, _remote_client
    from hive._daemon import DEFAULT_HOST

    payload = {
        "prompt": prompt,
        "model": model,
        "context": context,
        "max_tokens": max_tokens,
        "timeout_s": timeout_s,
        "structured": True,
    }

    # Reuse the shim's own detection rather than adding a second probe: two
    # answers to "is the daemon up" are two answers that can disagree.
    state = _read_state()
    if state is not None and _daemon_reachable(DEFAULT_HOST, state[0]):
        port, token = state
        # The fallback is PRE-SUBMISSION ONLY, and the flag is what enforces it.
        #
        # The daemon records usage as soon as the worker answers, before it
        # serialises the response. So a connection that dies after the request
        # went out leaves an *ambiguous* outcome: the inference may have run and
        # been paid for. Falling back there would run a second one — double
        # cost, and a second slot out of a pool whose concurrency is the binding
        # constraint. Retrying is only free before the request was submitted.
        #
        # Failing to open the session (daemon mid-restart, stale token,
        # handshake stall) is unambiguous: nothing ran, so the documented
        # ADR-011 §3 fallback applies and `degraded` reports which path answered.
        submitted = False
        try:
            client = _remote_client(DEFAULT_HOST, port, token)
            async with client:
                submitted = True
                result = await client.call_tool("delegate_task", payload)
                return _decode(result, degraded=False)
        except Exception as exc:  # noqa: BLE001 - classified by `submitted`
            if submitted:
                _log.warning("daemon connection lost after submission (%r); NOT retrying", exc)
                # task_failed, not pool_unavailable: exit 3 would advance the
                # dispatcher's chain and spend a second pool on a task that may
                # already have been served. Fail closed is the direction that
                # does not retry, and a human reading the detail can decide.
                return {
                    "status": "task_failed",
                    "model": model,
                    "degraded": False,
                    "tokens": 0,
                    "duration_ms": 0,
                    "output": "",
                    "detail": (
                        "the daemon connection failed after the request was submitted, so the "
                        f"worker may have run and been billed ({exc!r}). Not retried locally or "
                        "elsewhere: a second dispatch could pay for the same task twice."
                    ),
                }
            _log.warning("daemon session could not be opened (%r); falling back", exc)

    from hive.server import create_server

    local: Any = await create_server().call_tool("delegate_task", payload)
    return _decode(local, degraded=True)


def _decode(result: Any, *, degraded: bool) -> dict[str, Any]:
    """Turn a tool result into the record, tagging how it was reached.

    The tool answers with a JSON string in structured mode. A body that is not
    parseable is a task failure and not a pool problem: the pool answered, and
    what came back is unusable — retrying that on another model would hide it.
    """
    text = _result_text(result)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return _unusable(text, degraded, "unparseable")
    # Parseable is not the same as usable. `[]`, `null` and `"text"` are all
    # valid JSON and none is a record: assigning `degraded` into them raised
    # TypeError straight out of the verb, so a malformed worker body produced a
    # traceback on stderr and NOTHING on stdout — the one thing a dispatcher
    # cannot handle. An object missing contracted keys is the quieter version of
    # the same problem, and is rejected for the same reason.
    if not isinstance(parsed, dict):
        return _unusable(text, degraded, f"not a JSON object ({type(parsed).__name__})")
    missing = _REQUIRED_FIELDS - set(parsed)
    if missing:
        return _unusable(text, degraded, f"missing {', '.join(sorted(missing))}")
    record: dict[str, Any] = dict(parsed)
    record["degraded"] = degraded
    return record


def _unusable(text: str, degraded: bool, why: str) -> dict[str, Any]:
    """The canonical record for a body this verb cannot act on.

    task_failed rather than pool_unavailable: the pool answered, and what came
    back is unusable. Retrying that on a different model would hide it.
    """
    return {
        "status": "task_failed",
        "model": "",
        "degraded": degraded,
        "tokens": 0,
        "duration_ms": 0,
        "output": "",
        "detail": f"worker returned a body this verb cannot use — {why}: {text[:200]}",
    }


def _result_text(result: Any) -> str:
    """Extract the text payload from a FastMCP tool result, in either shape."""
    content = getattr(result, "content", None)
    if content:
        return str(getattr(content[0], "text", ""))
    return str(result)
