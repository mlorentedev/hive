"""Tests for HiveSettings (pydantic-settings)."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from hive.config import HiveSettings


@pytest.fixture(autouse=True)
def _isolate_hive_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ambient hive configuration before every test.

    ``HiveSettings`` reads ``HIVE_*`` env vars plus the unprefixed
    ``VAULT_PATH`` deploy alias. A developer or deploy box with any of these
    set (a real ``VAULT_PATH`` is the normal case) would otherwise mask the
    hardcoded defaults and fail the default-value assertions — green in CI, red
    locally. Clearing here runs before each test body, so the override tests
    still set their own vars.

    No provider-named variable is stripped, because none is read any more
    (#391). Stripping one would imply a coupling that no longer exists.
    """
    for key in list(os.environ):
        if key.startswith("HIVE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("VAULT_PATH", raising=False)


class TestDefaults:
    def test_vault_path_default(self) -> None:
        assert HiveSettings().vault_path == Path.home() / "Projects" / "knowledge"

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
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reject HIVE_LOCK_TIMEOUT_S=0 (must be ≥1)."""
        monkeypatch.setenv("HIVE_LOCK_TIMEOUT_S", "0")
        with pytest.raises(ValidationError):
            HiveSettings()

    def test_hive_lock_timeout_s_validation_high(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reject HIVE_LOCK_TIMEOUT_S>600 (foot-gun protection)."""
        monkeypatch.setenv("HIVE_LOCK_TIMEOUT_S", "601")
        with pytest.raises(ValidationError):
            HiveSettings()

    def test_hive_wal_checkpoint_interval_s_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HIVE_WAL_CHECKPOINT_INTERVAL_S env honored (HIVE-115 / ADR-009)."""
        monkeypatch.setenv("HIVE_WAL_CHECKPOINT_INTERVAL_S", "15.5")
        assert HiveSettings().wal_checkpoint_interval_s == 15.5

    def test_hive_post_kill_drain_s_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HIVE_POST_KILL_DRAIN_S env honored (HIVE-116 / ADR-012)."""
        monkeypatch.setenv("HIVE_POST_KILL_DRAIN_S", "7.5")
        assert HiveSettings().post_kill_drain_s == 7.5

    def test_hive_post_kill_drain_s_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Default is 5.0s (HIVE_OUTBOX_TICK_S symmetry, user-locked 2026-05-27)."""
        monkeypatch.delenv("HIVE_POST_KILL_DRAIN_S", raising=False)
        assert HiveSettings().post_kill_drain_s == 5.0

    @pytest.mark.parametrize("value", ["0.1", "0", "31", "60"])
    def test_hive_post_kill_drain_s_validation_rejects(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        """Out-of-range values raise ValidationError ([0.5, 30.0])."""
        monkeypatch.setenv("HIVE_POST_KILL_DRAIN_S", value)
        with pytest.raises(ValidationError):
            HiveSettings()

    @pytest.mark.parametrize("value", ["0.5", "5.0", "30.0"])
    def test_hive_post_kill_drain_s_validation_accepts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        """Boundary + default accepted."""
        monkeypatch.setenv("HIVE_POST_KILL_DRAIN_S", value)
        assert HiveSettings().post_kill_drain_s == float(value)

    def test_hive_prefix_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The HIVE_ prefix reaches a plain field (was HIVE_OLLAMA_MODEL)."""
        monkeypatch.setenv("HIVE_WORKER_MODEL", "deepseek-v4-flash")
        assert HiveSettings().worker_model == "deepseek-v4-flash"

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


class TestVaultScopesValidation:
    """Startup validation of HIVE_VAULT_SCOPES (#159 item 2).

    A clear ValidationError at construction beats undefined behaviour later:
    two scopes sharing a directory makes the second silently unreachable
    (auto-scan is first-match), and a directory that escapes the vault root
    is a foot-gun the path-boundary check would only catch lazily, per call.
    """

    def test_accepts_valid_custom_mapping(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "HIVE_VAULT_SCOPES",
            '{"projects": "10_projects", "nested": "50_work/clients"}',
        )
        assert HiveSettings().vault_scopes == {
            "projects": "10_projects",
            "nested": "50_work/clients",
        }

    def test_accepts_empty_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty mapping (flat vault, no scope routing) is allowed."""
        monkeypatch.setenv("HIVE_VAULT_SCOPES", "{}")
        assert HiveSettings().vault_scopes == {}

    def test_rejects_duplicate_directories(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two scope keys pointing at one directory shadows the second."""
        monkeypatch.setenv(
            "HIVE_VAULT_SCOPES",
            '{"projects": "10_projects", "alias": "10_projects"}',
        )
        with pytest.raises(ValidationError, match="same directory"):
            HiveSettings()

    @pytest.mark.parametrize(
        "bad_dir",
        ["../escape", "10_projects/../../etc", "sub/../../out"],
    )
    def test_rejects_parent_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bad_dir: str,
    ) -> None:
        monkeypatch.setenv("HIVE_VAULT_SCOPES", f'{{"x": "{bad_dir}"}}')
        with pytest.raises(ValidationError, match="relative path inside"):
            HiveSettings()

    @pytest.mark.parametrize("bad_dir", ["/etc", "\\\\windows", "C:/data"])
    def test_rejects_absolute_directories(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bad_dir: str,
    ) -> None:
        monkeypatch.setenv("HIVE_VAULT_SCOPES", f'{{"x": "{bad_dir}"}}')
        with pytest.raises(ValidationError, match="relative path inside"):
            HiveSettings()

    @pytest.mark.parametrize("bad_dir", ["", "   "])
    def test_rejects_blank_directories(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bad_dir: str,
    ) -> None:
        monkeypatch.setenv("HIVE_VAULT_SCOPES", f'{{"x": "{bad_dir}"}}')
        with pytest.raises(ValidationError, match="relative path inside"):
            HiveSettings()


class TestLogPath:
    def test_log_path_default(self) -> None:
        expected = str(Path.home() / ".local" / "share" / "hive" / "hive.log")
        assert HiveSettings().log_path == expected

    def test_log_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_LOG_PATH", "/tmp/hive-test.log")
        assert HiveSettings().log_path == "/tmp/hive-test.log"

    def test_log_retention_days_default(self) -> None:
        assert HiveSettings().log_retention_days == 7

    def test_log_retention_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_LOG_RETENTION_DAYS", "30")
        assert HiveSettings().log_retention_days == 30

    @pytest.mark.parametrize("bad", ["0", "366", "-1"])
    def test_log_retention_days_rejects_out_of_range(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bad: str,
    ) -> None:
        """Bounded 1..365: a 0 would collect the log being written this second,
        and an unbounded value silently disables the GC that #329 exists for."""
        monkeypatch.setenv("HIVE_LOG_RETENTION_DAYS", bad)
        with pytest.raises(ValidationError):
            HiveSettings()


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


class TestWorkerSettings:
    """HIVE-384: the worker's own provider settings, with the embed fallback.

    The worker is not an embedder, so it gets honest names — but the two point
    at the same NaN endpoint in the default deployment, and requiring both to be
    set would mean a machine that already works stops working on upgrade. Hence
    the fallback: ``HIVE_WORKER_*`` when present, ``HIVE_EMBED_*`` otherwise.
    """

    def test_falls_back_to_embed_when_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HIVE_EMBED_BASE_URL", "https://api.nan.example/v1")
        monkeypatch.setenv("HIVE_EMBED_API_KEY", "embed-key")
        monkeypatch.setenv("HIVE_EMBED_MODEL", "qwen3-embedding")
        s = HiveSettings()
        assert s.worker_base_url == "https://api.nan.example/v1"
        assert s.worker_api_key == "embed-key"
        assert s.worker_model == "qwen3-embedding"

    def test_explicit_worker_settings_win_over_embed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HIVE_EMBED_BASE_URL", "https://embed.example/v1")
        monkeypatch.setenv("HIVE_EMBED_API_KEY", "embed-key")
        monkeypatch.setenv("HIVE_EMBED_MODEL", "qwen3-embedding")
        monkeypatch.setenv("HIVE_WORKER_BASE_URL", "https://worker.example/v1")
        monkeypatch.setenv("HIVE_WORKER_API_KEY", "worker-key")
        monkeypatch.setenv("HIVE_WORKER_MODEL", "deepseek-v4-flash")
        s = HiveSettings()
        assert s.worker_base_url == "https://worker.example/v1"
        assert s.worker_api_key == "worker-key"
        assert s.worker_model == "deepseek-v4-flash"

    # The provider-named alias this credential once accepted is gone (#391);
    # tests/test_provider_neutrality.py asserts it stays gone.

    def test_all_empty_is_the_disabled_default(self) -> None:
        """No worker configuration at all resolves to empty, never to a guess."""
        s = HiveSettings()
        assert s.worker_base_url == ""
        assert s.worker_api_key == ""
        assert s.worker_model == ""


class TestRetiredProviderSettings:
    """HIVE-384: Ollama and OpenRouter are removed, not deprecated.

    Asserted rather than reviewed by eye, so a partial revert is caught by the
    suite instead of by a reader.
    """

    @pytest.mark.parametrize(
        "field",
        [
            "ollama_endpoint",
            "ollama_model",
            "openrouter_api_key",
            "openrouter_budget",
            "openrouter_model",
            "openrouter_paid_model",
        ],
    )
    def test_retired_field_is_gone(self, field: str) -> None:
        assert field not in HiveSettings.model_fields
