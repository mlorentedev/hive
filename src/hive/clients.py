"""HTTP clients for OpenAI-compatible providers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


class PoolUnavailableError(ConnectionError):
    """The pool declined to serve the request; retrying elsewhere is legitimate.

    Unreachable, timed out, rate-limited, or credential-rejected — in every one
    of those the request never became an answer, so a dispatcher should advance
    its fallback chain. That is the whole reason this type exists as something
    other than a message: HIVE-384's contract gives it exit `3`, while a worker
    that *answered* with a failure gets exit `1` and no retry.

    It subclasses ``ConnectionError`` deliberately. Both `_try_worker` and
    `worker_status` already catch `ConnectionError` and treat it as *pool
    unavailable*; a sibling type would have silently reclassified them into the
    task-failed branch — the exact collapse this distinction exists to prevent.

    Deliberately NOT raised for 5xx: those mean the pool accepted the request
    and broke while serving it. Unknown failures classify as task-failed,
    because the fail-closed direction is the one that does not retry.
    """


# HTTP statuses that mean "this pool did not serve you", as opposed to "your
# request was served and the result is bad". Kept as a mapping so the raised
# message names which refusal it was: an operator reading one line should not
# have to look the number up, and "rate limited" and "credential rejected" call
# for completely different actions.
_POOL_REFUSAL_STATUSES = {
    401: "credential rejected",
    403: "forbidden",
    429: "rate limited",
}

_AVAILABILITY_CACHE_TTL_S = 30.0
# Health-check connect timeout: kept short so an unreachable endpoint
# fails fast (1s) instead of consuming the generate-timeout budget.
_AVAILABILITY_CONNECT_TIMEOUT_S = 1.0
# Label used when a base_url carries no parseable host, so an error message
# never degrades to an empty prefix like " unavailable: ...".
_UNKNOWN_PROVIDER_LABEL = "OpenAI-compatible endpoint"


def provider_label(base_url: str) -> str:
    """Describe a provider by its host, for error messages and logs.

    The host is derived rather than declared on purpose: a label configured
    separately from the endpoint it names is a second thing to keep truthful,
    and it goes stale the moment someone repoints ``base_url`` without
    updating it. Deriving it means the name in an error is always the machine
    the request actually went to.
    """
    host = urlsplit(base_url).hostname or ""
    return host or _UNKNOWN_PROVIDER_LABEL


@dataclass(frozen=True)
class ClientResponse:
    """Unified response from any LLM provider."""

    text: str
    model: str
    tokens: int
    cost_usd: float
    latency_ms: int


@dataclass(frozen=True)
class EmbedResponse:
    """Unified embedding response from any OpenAI-compatible provider."""

    vectors: list[list[float]]
    model: str
    tokens: int
    latency_ms: int


@dataclass(frozen=True)
class ModelInfo:
    """Model metadata from an OpenAI-compatible ``/v1/models`` catalog."""

    id: str
    name: str
    context_length: int
    cost_per_million_input: float
    cost_per_million_output: float
    is_free: bool


class OpenAICompatibleClient:
    """Async client for any OpenAI-compatible ``/v1`` API.

    Hive names no provider: whatever serves an OpenAI-compatible ``/v1`` is a
    valid backend, and which one is a deployment choice made entirely through
    configuration. A hosted service and a local runtime are equally supported.

    ``base_url`` includes the version prefix and everything up to the resource
    (e.g. ``https://api.example.com/v1`` or ``http://localhost:11434/v1``).
    Full request URLs are built explicitly as ``{base_url}/chat/completions`` etc.
    rather than relying on httpx's RFC-3986 ``base_url`` join, which silently
    drops a base path when the request path is absolute.

    ``provider_name`` only ever labels errors and logs. Leave it unset and it
    is derived from ``base_url``'s host, which cannot disagree with the
    endpoint the request went to; pass it to give a client a role name instead
    (``embed`` / ``synth``), where which of several clients failed matters more
    than which host did.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        default_model: str = "",
        timeout: float = 120.0,
        provider_name: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._provider_name = provider_name or provider_label(base_url)
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=timeout, headers=headers)

    @property
    def model(self) -> str:
        """The default model id this client was configured with."""
        return self._default_model

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _post_json(
        self, resource: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        """POST ``payload`` to ``{base_url}/{resource}``; translate errors uniformly.

        Returns ``(parsed_json, elapsed_ms)``. Raises ``ConnectionError`` on any
        transport failure (including ``ReadTimeout``) and ``RuntimeError`` on a
        non-2xx status or a non-JSON body.
        """
        url = f"{self._base_url}/{resource}"
        try:
            start = time.monotonic()
            resp = await self._http.post(url, json=payload)
            elapsed_ms = int((time.monotonic() - start) * 1000)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise PoolUnavailableError(f"{self._provider_name} unavailable: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise PoolUnavailableError(f"{self._provider_name} request timed out: {exc}") from exc

        # A refusal is not a failed task. 429 and 401/403 mean the pool did not
        # serve this request at all, so a dispatcher should try the next entry
        # in its chain; a 4xx about the request itself, or any 5xx, means the
        # pool took it and the answer is unusable, which must not be retried
        # elsewhere. Before HIVE-384 PR 2 every non-2xx raised RuntimeError, so
        # a rate limit read as a broken task and the chain stopped exactly
        # where it should have advanced.
        if resp.status_code in _POOL_REFUSAL_STATUSES:
            raise PoolUnavailableError(
                f"{self._provider_name} refused the request ({resp.status_code} "
                f"{_POOL_REFUSAL_STATUSES[resp.status_code]}): {self._error_detail(resp)}"
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"{self._provider_name} API error ({resp.status_code}): {self._error_detail(resp)}"
            )
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"{self._provider_name} returned non-JSON response: {exc}") from exc
        return data, elapsed_ms

    def _error_detail(self, resp: httpx.Response) -> str:
        """Best-effort error message from a non-2xx response body, redacted.

        The body is a REMOTE string that this process then puts into an
        exception message, a log line and a result record. Some providers echo
        the request's ``Authorization`` header back in an auth-failure body, so
        the one call most likely to produce a detail — a 401 — is also the one
        most likely to carry the credential in it (AC7).

        The redaction is exact rather than pattern-based: it removes this
        client's own key, which cannot over-redact and cannot miss a token
        shaped differently than expected. A general secret scrubber over
        arbitrary strings is a bigger thing and is not this.
        """
        detail = self._extract_detail(resp)
        if self._api_key and self._api_key in detail:
            detail = detail.replace(self._api_key, "<redacted>")
        return detail

    @staticmethod
    def _extract_detail(resp: httpx.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return resp.text[:200]
        error = data.get("error", {}) if isinstance(data, dict) else {}
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
        return resp.text[:200]

    async def generate(
        self,
        prompt: str,
        context: str = "",
        model: str | None = None,
        max_tokens: int = 2000,
    ) -> ClientResponse:
        """Send a chat completion and return a unified response."""
        resolved_model = model or self._default_model
        messages: list[dict[str, str]] = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        data, elapsed_ms = await self._post_json(
            "chat/completions",
            {"model": resolved_model, "messages": messages, "max_tokens": max_tokens},
        )
        usage = data.get("usage", {})
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            msg = f"{self._provider_name} response missing expected fields: {exc}"
            raise RuntimeError(msg) from exc

        return ClientResponse(
            text=text,
            model=data.get("model", resolved_model),
            tokens=usage.get("total_tokens", 0),
            cost_usd=usage.get("cost", 0.0),
            latency_ms=elapsed_ms,
        )

    async def embed(self, texts: list[str], model: str = "") -> EmbedResponse:
        """Embed ``texts`` via the provider's ``/embeddings`` endpoint."""
        resolved_model = model or self._default_model
        data, elapsed_ms = await self._post_json(
            "embeddings", {"model": resolved_model, "input": texts}
        )
        items = data.get("data", [])
        # Vector<->text alignment is load-bearing for RAG: a provider may return
        # ``data`` out of order, so sort by ``index`` before extracting vectors.
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        try:
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError) as exc:
            msg = f"{self._provider_name} embeddings response missing 'embedding': {exc}"
            raise RuntimeError(msg) from exc
        usage = data.get("usage", {})

        return EmbedResponse(
            vectors=vectors,
            model=data.get("model", resolved_model),
            tokens=usage.get("total_tokens", 0),
            latency_ms=elapsed_ms,
        )

    async def list_models(self) -> list[ModelInfo]:
        """Fetch available models from the provider's ``/models`` catalog."""
        try:
            resp = await self._http.get(f"{self._base_url}/models")
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            msg = f"{self._provider_name} unavailable: {exc}"
            raise PoolUnavailableError(msg) from exc
        except httpx.TimeoutException as exc:
            msg = f"{self._provider_name} request timed out: {exc}"
            raise PoolUnavailableError(msg) from exc

        # Same split as _post_json: worker_status reads reachability off this
        # call, and a rate-limited catalog means "reachable, throttled" rather
        # than "broken".
        if resp.status_code in _POOL_REFUSAL_STATUSES:
            msg = (
                f"{self._provider_name} refused the models request "
                f"({resp.status_code} {_POOL_REFUSAL_STATUSES[resp.status_code]})"
            )
            raise PoolUnavailableError(msg)
        if resp.status_code >= 400:
            msg = f"{self._provider_name} models error ({resp.status_code}): {resp.text[:200]}"
            raise RuntimeError(msg)

        try:
            data = resp.json()
        except ValueError as exc:
            msg = f"{self._provider_name} returned invalid JSON: {exc}"
            raise RuntimeError(msg) from exc
        models: list[ModelInfo] = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            try:
                input_cost = float(pricing.get("prompt", "0"))
                output_cost = float(pricing.get("completion", "0"))
            except (ValueError, TypeError):
                input_cost = 0.0
                output_cost = 0.0
            models.append(
                ModelInfo(
                    id=m["id"],
                    name=m.get("name", m["id"]),
                    context_length=m.get("context_length", 0),
                    cost_per_million_input=input_cost * 1_000_000,
                    cost_per_million_output=output_cost * 1_000_000,
                    is_free=(input_cost == 0.0 and output_cost == 0.0),
                )
            )
        return models
