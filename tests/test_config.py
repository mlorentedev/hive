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
    def test_hive_lock_timeout_s_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HIVE_LOCK_TIMEOUT_S env honored end-to-end (HIVE-115 / ADR-010)."""
        monkeypatch.setenv("HIVE_LOCK_TIMEOUT_S", "60")
        s = HiveSettings()
        assert s.lock_timeout_s == 60

    def test_hive_lock_timeout_s_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HIVE_LOCK_TIMEOUT_S", raising=False)
        assert HiveSettings().lock_timeout_s == 30

    def test_hive_lock_timeout_s_validation_low(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reject HIVE_LOCK_TIMEOUT_S=0 (must be ≥1)."""
        monkeypatch.setenv("HIVE_LOCK_TIMEOUT_S", "0")
        with pytest.raises(ValidationError):
            HiveSettings()

    def test_hive_lock_timeout_s_validation_high(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reject HIVE_LOCK_TIMEOUT_S>600 (foot-gun protection)."""
        monkeypatch.setenv("HIVE_LOCK_TIMEOUT_S", "601")
        with pytest.raises(ValidationError):
            HiveSettings()

    def test_hive_wal_checkpoint_interval_s_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HIVE_WAL_CHECKPOINT_INTERVAL_S env honored (HIVE-115 / ADR-009)."""
        monkeypatch.setenv("HIVE_WAL_CHECKPOINT_INTERVAL_S", "15.5")
        assert HiveSettings().wal_checkpoint_interval_s == 15.5

    def test_hive_post_kill_drain_s_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HIVE_POST_KILL_DRAIN_S env honored (HIVE-116 / ADR-012)."""
        monkeypatch.setenv("HIVE_POST_KILL_DRAIN_S", "7.5")
        assert HiveSettings().post_kill_drain_s == 7.5

    def test_hive_post_kill_drain_s_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Default is 5.0s (HIVE_OUTBOX_TICK_S symmetry, user-locked 2026-05-27)."""
        monkeypatch.delenv("HIVE_POST_KILL_DRAIN_S", raising=False)
        assert HiveSettings().post_kill_drain_s == 5.0

    @pytest.mark.parametrize("value", ["0.1", "0", "31", "60"])
    def test_hive_post_kill_drain_s_validation_rejects(
        self, monkeypatch: pytest.MonkeyPatch, value: str,
    ) -> None:
        """Out-of-range values raise ValidationError ([0.5, 30.0])."""
        monkeypatch.setenv("HIVE_POST_KILL_DRAIN_S", value)
        with pytest.raises(ValidationError):
            HiveSettings()

    @pytest.mark.parametrize("value", ["0.5", "5.0", "30.0"])
    def test_hive_post_kill_drain_s_validation_accepts(
        self, monkeypatch: pytest.MonkeyPatch, value: str,
    ) -> None:
        """Boundary + default accepted."""
        monkeypatch.setenv("HIVE_POST_KILL_DRAIN_S", value)
        assert HiveSettings().post_kill_drain_s == float(value)

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

    def test_vault_path_without_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HIVE_VAULT_PATH", raising=False)
        monkeypatch.setenv("VAULT_PATH", "/tmp/bare-vault")
        assert HiveSettings().vault_path == Path("/tmp/bare-vault")

    def test_hive_vault_path_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAULT_PATH", "/tmp/bare")
        monkeypatch.setenv("HIVE_VAULT_PATH", "/tmp/prefixed")
        assert HiveSettings().vault_path == Path("/tmp/prefixed")


class TestVaultScopes:
    def test_default_scopes(self) -> None:
        s = HiveSettings()
        assert s.vault_scopes == {
            "projects": "10_projects",
            "meta": "00_meta",
            "work": "50_work",
            "agents": "80_agents",
        }

    def test_agents_scope_appended_last(self) -> None:
        """agents must be the LAST key so auto-scan (first-match) cannot let
        an agent dir shadow an existing projects/work project name."""
        assert list(HiveSettings().vault_scopes) == [
            "projects",
            "meta",
            "work",
            "agents",
        ]

    def test_scopes_env_can_add_agents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HIVE_VAULT_SCOPES JSON env can introduce an agents scope (req #5)."""
        monkeypatch.setenv(
            "HIVE_VAULT_SCOPES",
            '{"projects": "10_projects", "agents": "80_agents"}',
        )
        assert HiveSettings().vault_scopes == {
            "projects": "10_projects",
            "agents": "80_agents",
        }

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


class TestLogPath:
    def test_log_path_default(self) -> None:
        expected = str(Path.home() / ".local" / "share" / "hive" / "hive.log")
        assert HiveSettings().log_path == expected

    def test_log_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_LOG_PATH", "/tmp/hive-test.log")
        assert HiveSettings().log_path == "/tmp/hive-test.log"


class TestNewDefaults:
    def test_stale_threshold_days_default(self) -> None:
        assert HiveSettings().stale_threshold_days == 180

    def test_stale_threshold_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_STALE_THRESHOLD_DAYS", "90")
        assert HiveSettings().stale_threshold_days == 90

    def test_http_timeout_default(self) -> None:
        assert HiveSettings().http_timeout == 60.0

    def test_http_timeout_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_HTTP_TIMEOUT", "30")
        assert HiveSettings().http_timeout == 30.0

    def test_tool_timeout_default(self) -> None:
        assert HiveSettings().tool_timeout == 60.0

    def test_tool_timeout_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_TOOL_TIMEOUT", "30")
        assert HiveSettings().tool_timeout == 30.0

    def test_relevance_alpha_default(self) -> None:
        assert HiveSettings().relevance_alpha == 0.3

    def test_relevance_decay_default(self) -> None:
        assert HiveSettings().relevance_decay == 0.9

    def test_relevance_epsilon_default(self) -> None:
        assert HiveSettings().relevance_epsilon == 0.15


class TestValidation:
    def test_invalid_budget_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            HiveSettings(openrouter_budget="not-a-number")  # type: ignore[arg-type]
