"""HTTP clients for Ollama and OpenAI-compatible providers (OpenRouter, NaN)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

_AVAILABILITY_CACHE_TTL_S = 30.0
# Health-check connect timeout: kept short so an unreachable Ollama
# fails fast (1s) instead of consuming the generate-timeout budget.
_AVAILABILITY_CONNECT_TIMEOUT_S = 1.0


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
    """Model metadata from OpenRouter catalog."""

    id: str
    name: str
    context_length: int
    cost_per_million_input: float
    cost_per_million_output: float
    is_free: bool


class OllamaClient:
    """Async client for Ollama's /api/chat endpoint."""

    def __init__(self, endpoint: str, model: str, timeout: float = 120.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._http = httpx.AsyncClient(base_url=self._endpoint, timeout=timeout)
        # is_available probe uses a much shorter connect timeout so an
        # unreachable homelab fails in ~1s rather than burning the full
        # generate timeout; the read timeout still matches generate
        # because the / endpoint should respond instantly.
        self._probe_http = httpx.AsyncClient(
            base_url=self._endpoint,
            timeout=httpx.Timeout(
                connect=_AVAILABILITY_CONNECT_TIMEOUT_S,
                read=_AVAILABILITY_CONNECT_TIMEOUT_S,
                write=_AVAILABILITY_CONNECT_TIMEOUT_S,
                pool=_AVAILABILITY_CONNECT_TIMEOUT_S,
            ),
        )
        self._availability_cached: bool | None = None
        self._availability_cached_at: float = 0.0

    @property
    def model(self) -> str:
        """The configured model name."""
        return self._model

    async def aclose(self) -> None:
        """Close the underlying HTTP clients."""
        await self._http.aclose()
        await self._probe_http.aclose()

    async def generate(
        self, prompt: str, context: str = "", max_tokens: int = 2000
    ) -> ClientResponse:
        """Send a chat completion to Ollama and return a unified response."""
        messages: list[dict[str, str]] = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        try:
            start = time.monotonic()
            resp = await self._http.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            msg = f"Ollama unavailable at {self._endpoint}: {exc}"
            raise ConnectionError(msg) from exc
        except httpx.TimeoutException as exc:
            msg = f"Ollama request timed out at {self._endpoint}: {exc}"
            raise ConnectionError(msg) from exc

        if resp.status_code >= 400:
            msg = f"Ollama error ({resp.status_code}): {resp.text[:200]}"
            raise RuntimeError(msg)

        try:
            data = resp.json()
        except ValueError as exc:
            msg = f"Ollama returned non-JSON response: {exc}"
            raise RuntimeError(msg) from exc
        # Ollama returns total_duration in nanoseconds
        total_ns = data.get("total_duration", 0)
        latency = int(total_ns / 1_000_000) if total_ns else elapsed_ms

        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            msg = f"Ollama response missing expected fields: {exc}"
            raise RuntimeError(msg) from exc

        return ClientResponse(
            text=text,
            model=self._model,
            tokens=data.get("eval_count", 0),
            cost_usd=0.0,
            latency_ms=latency,
        )

    async def is_available(self) -> bool:
        """Check if Ollama is reachable.

        Result is cached for ``_AVAILABILITY_CACHE_TTL_S`` seconds to
        avoid storming the endpoint when multiple tool calls (or
        multiple hive subprocesses) all probe within the same window.
        Uses a short connect timeout so an outage answers in ~1s
        instead of consuming the generate-timeout budget.
        """
        now = time.monotonic()
        if (
            self._availability_cached is not None
            and now - self._availability_cached_at < _AVAILABILITY_CACHE_TTL_S
        ):
            return self._availability_cached
        try:
            resp = await self._probe_http.get("/")
            available = resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            available = False
        self._availability_cached = available
        self._availability_cached_at = now
        return available


class OpenAICompatibleClient:
    """Async client for any OpenAI-compatible ``/v1`` API (OpenRouter, NaN, Ollama).

    ``base_url`` includes the version prefix and everything up to the resource
    (e.g. ``https://api.nan.builders/v1`` or ``https://openrouter.ai/api/v1``).
    Full request URLs are built explicitly as ``{base_url}/chat/completions`` etc.
    rather than relying on httpx's RFC-3986 ``base_url`` join, which silently
    drops a base path when the request path is absolute.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        default_model: str = "",
        timeout: float = 120.0,
        provider_name: str = "OpenAI-compatible",
        title: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._provider_name = provider_name
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if title:
            headers["X-OpenRouter-Title"] = title
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=timeout, headers=headers)

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
            raise ConnectionError(f"{self._provider_name} unavailable: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ConnectionError(f"{self._provider_name} request timed out: {exc}") from exc

        if resp.status_code == 429:
            raise RuntimeError(f"{self._provider_name} rate limit exceeded. Retry later.")
        if resp.status_code >= 400:
            raise RuntimeError(
                f"{self._provider_name} API error ({resp.status_code}): {self._error_detail(resp)}"
            )
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"{self._provider_name} returned non-JSON response: {exc}") from exc
        return data, elapsed_ms

    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        """Best-effort error message from a non-2xx response body."""
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
            raise ConnectionError(msg) from exc
        except httpx.TimeoutException as exc:
            msg = f"{self._provider_name} request timed out: {exc}"
            raise ConnectionError(msg) from exc

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


class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter client — a thin, backward-compatible OpenAI-compatible subclass.

    Preserves the original constructor signature and the ``X-OpenRouter-Title``
    header so existing callers and tests are unaffected.
    """

    def __init__(self, api_key: str, default_model: str, timeout: float = 120.0) -> None:
        super().__init__(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_model=default_model,
            timeout=timeout,
            provider_name="OpenRouter",
            title="hive-worker",
        )
