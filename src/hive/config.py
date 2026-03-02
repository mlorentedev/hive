"""Hive configuration — resolved from environment variables with sensible defaults."""

import os
from pathlib import Path


def _resolve_vault_path() -> Path:
    """Resolve vault path: env var > default ~/Projects/knowledge."""
    env = os.environ.get("HIVE_VAULT_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / "Projects" / "knowledge"


def _resolve_ollama_endpoint() -> str:
    """Resolve Ollama endpoint: env var > default localhost."""
    return os.environ.get("HIVE_OLLAMA_ENDPOINT", "http://localhost:11434")


def _resolve_openrouter_key() -> str | None:
    """Resolve OpenRouter API key from environment."""
    return os.environ.get("OPENROUTER_API_KEY")


def _resolve_openrouter_budget() -> float:
    """Monthly budget cap for OpenRouter in USD."""
    return float(os.environ.get("HIVE_OPENROUTER_BUDGET", "10.0"))


VAULT_PATH: Path = _resolve_vault_path()
OLLAMA_ENDPOINT: str = _resolve_ollama_endpoint()
OPENROUTER_API_KEY: str | None = _resolve_openrouter_key()
OPENROUTER_BUDGET_USD: float = _resolve_openrouter_budget()
OPENROUTER_MODEL: str = os.environ.get("HIVE_OPENROUTER_MODEL", "qwen/qwen-2.5-coder-32b-instruct")
