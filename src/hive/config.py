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
    return float(os.environ.get("HIVE_OPENROUTER_BUDGET", "5.0"))


def _resolve_db_path() -> str:
    """Resolve SQLite database path for worker budget tracking."""
    env = os.environ.get("HIVE_DB_PATH")
    if env:
        return env
    return str(Path.home() / ".local" / "share" / "hive" / "worker.db")


# Vault config
VAULT_PATH: Path = _resolve_vault_path()

# Ollama config
OLLAMA_ENDPOINT: str = _resolve_ollama_endpoint()
OLLAMA_MODEL: str = os.environ.get("HIVE_OLLAMA_MODEL", "qwen2.5-coder:3b")

# OpenRouter config
OPENROUTER_API_KEY: str | None = _resolve_openrouter_key()
OPENROUTER_BUDGET_USD: float = _resolve_openrouter_budget()
OPENROUTER_MODEL: str = os.environ.get("HIVE_OPENROUTER_MODEL", "qwen/qwen3-coder:free")

# Worker config
DB_PATH: str = _resolve_db_path()
