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
