"""Tests for HiveSettings (pydantic-settings)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hive.config import HiveSettings


class TestDefaults:
    def test_vault_path_default(self) -> None:
        s = HiveSettings()
        assert s.vault_path == Path.home() / "Projects" / "knowledge"

    def test_ollama_endpoint_default(self) -> None:
        assert HiveSettings().ollama_endpoint == "http://localhost:11434"

    def test_ollama_model_default(self) -> None:
        assert HiveSettings().ollama_model == "qwen2.5-coder:7b"

    def test_openrouter_api_key_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("HIVE_OPENROUTER_API_KEY", raising=False)
        assert HiveSettings().openrouter_api_key is None

    def test_openrouter_budget_default(self) -> None:
        assert HiveSettings().openrouter_budget == 1.0

    def test_openrouter_model_default(self) -> None:
        assert HiveSettings().openrouter_model == "qwen/qwen3-coder:free"

    def test_db_path_default(self) -> None:
        expected = str(Path.home() / ".local" / "share" / "hive" / "worker.db")
        assert HiveSettings().db_path == expected


class TestEnvOverride:
    def test_hive_prefix_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_OLLAMA_MODEL", "llama3:8b")
        assert HiveSettings().ollama_model == "llama3:8b"

    def test_openrouter_key_without_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
        assert HiveSettings().openrouter_api_key == "sk-test-123"

    def test_hive_prefixed_key_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "bare")
        monkeypatch.setenv("HIVE_OPENROUTER_API_KEY", "prefixed")
        assert HiveSettings().openrouter_api_key == "prefixed"

    def test_vault_path_coercion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_VAULT_PATH", "/tmp/vault")
        assert HiveSettings().vault_path == Path("/tmp/vault")


class TestVaultScopes:
    def test_default_scopes(self) -> None:
        s = HiveSettings()
        assert s.vault_scopes == {"projects": "10_projects", "meta": "00_meta"}

    def test_scopes_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "HIVE_VAULT_SCOPES",
            '{"projects": "10_projects", "meta": "00_meta", "work": "50_work"}',
        )
        s = HiveSettings()
        assert s.vault_scopes == {
            "projects": "10_projects",
            "meta": "00_meta",
            "work": "50_work",
        }


class TestValidation:
    def test_invalid_budget_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            HiveSettings(openrouter_budget="not-a-number")  # type: ignore[arg-type]
