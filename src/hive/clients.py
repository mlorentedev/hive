"""HTTP clients for Ollama and OpenRouter APIs."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ClientResponse:
    """Unified response from any LLM provider."""

    text: str
    model: str
    tokens: int
    cost_usd: float
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

        data = resp.json()
        # Ollama returns total_duration in nanoseconds
        total_ns = data.get("total_duration", 0)
        latency = int(total_ns / 1_000_000) if total_ns else elapsed_ms

        return ClientResponse(
            text=data["message"]["content"],
            model=self._model,
            tokens=data.get("eval_count", 0),
            cost_usd=0.0,
            latency_ms=latency,
        )

    async def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            resp = await self._http.get("/")
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return False


class OpenRouterClient:
    """Async client for OpenRouter's chat completions API."""

    _BASE_URL = "https://openrouter.ai"

    def __init__(self, api_key: str, default_model: str, timeout: float = 120.0) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._http = httpx.AsyncClient(
            base_url=self._BASE_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-OpenRouter-Title": "hive-worker",
            },
        )

    async def generate(
        self,
        prompt: str,
        context: str = "",
        model: str | None = None,
        max_tokens: int = 2000,
    ) -> ClientResponse:
        """Send a chat completion to OpenRouter and return a unified response."""
        resolved_model = model or self._default_model
        messages: list[dict[str, str]] = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        try:
            start = time.monotonic()
            resp = await self._http.post(
                "/api/v1/chat/completions",
                json={
                    "model": resolved_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            msg = f"OpenRouter unavailable: {exc}"
            raise ConnectionError(msg) from exc

        if resp.status_code == 429:
            msg = "OpenRouter rate limit exceeded. Retry later."
            raise RuntimeError(msg)

        if resp.status_code >= 400:
            data = resp.json()
            error_msg = data.get("error", {}).get("message", resp.text)
            msg = f"OpenRouter API error ({resp.status_code}): {error_msg}"
            raise RuntimeError(msg)

        data = resp.json()
        usage = data.get("usage", {})

        return ClientResponse(
            text=data["choices"][0]["message"]["content"],
            model=data.get("model", resolved_model),
            tokens=usage.get("total_tokens", 0),
            cost_usd=usage.get("cost", 0.0),
            latency_ms=elapsed_ms,
        )

    async def list_models(self) -> list[ModelInfo]:
        """Fetch available models from the OpenRouter catalog."""
        try:
            resp = await self._http.get("/api/v1/models")
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            msg = f"OpenRouter unavailable: {exc}"
            raise ConnectionError(msg) from exc

        data = resp.json()
        models: list[ModelInfo] = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            input_cost = float(pricing.get("prompt", "0"))
            output_cost = float(pricing.get("completion", "0"))
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
