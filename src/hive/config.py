"""Hive configuration — pydantic-settings with env var overrides."""

import re
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A leading drive letter (``C:``) marks a Windows absolute path; checked
# explicitly because ``str.startswith`` only catches the separator forms.
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class HiveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HIVE_")

    vault_path: Path = Field(
        default=Path.home() / "Projects" / "knowledge",
        validation_alias=AliasChoices("HIVE_VAULT_PATH", "VAULT_PATH"),
    )
    # HIVE-384: the worker's own endpoint, OpenAI-compatible. Which provider
    # serves it is a deployment choice hive does not make; the two hard-wired
    # providers this replaced are named in the 4.0.0 changelog, not here.
    #
    # Empty base_url = worker disabled, exactly as embed_base_url means for the
    # semantic backend. Never a guessed default: a worker that silently points
    # somewhere is worse than one that says it is unconfigured.
    worker_base_url: str = ""
    worker_model: str = ""
    # One name, and it is hive's own. An alias naming a particular provider
    # would make a published package read a variable that only means something
    # inside one deployment; mapping a launcher's own name onto
    # HIVE_WORKER_API_KEY is the launcher's job, at injection time.
    # repr=False on every credential field. `repr(settings)` is not something
    # anyone calls on purpose — it is what a traceback, a debug log and a
    # pytest assertion print unbidden, and pydantic renders field VALUES there.
    # Measured: `repr(HiveSettings())` contained `worker_api_key='<the key>'`
    # verbatim. The transcript that catches such a line is a durable artifact
    # nothing scans and nothing can un-print (AC7).
    #
    # `SecretStr` is the idiomatic fix and is deliberately NOT taken here: it
    # changes the field's type, which propagates through `ServerContext` and
    # six call sites, and a type migration does not belong in the PR that
    # discovered the leak. Tracked separately; `repr=False` closes the leak now.
    worker_api_key: str = Field(default="", repr=False)
    vault_scopes: dict[str, str] = Field(
        # ``agents`` is intentionally LAST: auto-scan (plain project name)
        # resolves first-match over insertion order, so appending keeps an
        # 80_agents/ dir from shadowing an existing projects/work project.
        # Override the whole mapping via HIVE_VAULT_SCOPES (JSON).
        default={
            "projects": "10_projects",
            "meta": "00_meta",
            "work": "50_work",
            "agents": "80_agents",
        },
    )
    # HIVE-211: optional semantic backend for vault_ask. Empty base_url =
    # disabled (the default) — no index, no embed-on-write, zero overhead.
    # OpenAI-compatible, and no provider is assumed: a hosted service and a
    # local runtime (e.g. HIVE_EMBED_BASE_URL=http://localhost:11434/v1) are
    # configured the same way.
    embed_base_url: str = ""
    embed_model: str = ""
    embed_api_key: str = Field(default="", repr=False)
    # HIVE-211 PR4: LLM synthesis model. Uses the same base_url/api_key as
    # embeddings. Empty = return formatted retrieval chunks (no LLM call).
    # The model id is whatever the configured endpoint calls it — hive does not
    # validate it against a catalog of known providers.
    synth_model: str = ""
    db_path: str = str(Path.home() / ".local" / "share" / "hive" / "worker.db")
    relevance_db_path: str = str(
        Path.home() / ".local" / "share" / "hive" / "relevance.db",
    )
    lesson_db_path: str = str(
        Path.home() / ".local" / "share" / "hive" / "lesson_reinforcement.db",
    )
    stale_threshold_days: int = 180
    http_timeout: float = 60.0
    tool_timeout: float = 60.0
    relevance_alpha: float = 0.3
    relevance_decay: float = 0.9
    relevance_epsilon: float = 0.15
    log_path: str = str(Path.home() / ".local" / "share" / "hive" / "hive.log")
    log_level: str = "INFO"
    # HIVE-329: retention for the per-PID debug logs. Each file is already
    # size-capped by RotatingFileHandler, so this bounds the file *count* —
    # the axis that was unbounded (one log per server start, forever).
    log_retention_days: int = Field(default=7, ge=1, le=365)
    # HIVE-115 / ADR-009: WAL checkpoint daemon thread interval.
    wal_checkpoint_interval_s: float = Field(default=30.0, gt=0.0, le=3600.0)
    # HIVE-115 / ADR-010: tunable lock acquire timeout (vault git operations).
    lock_timeout_s: int = Field(default=30, ge=1, le=600)
    # HIVE-116 / ADR-012: post-kill drain window before the supervisor evicts
    # the cached ``.git/hive.lock`` filelock from ``_GIT_FILELOCKS``. Window
    # must be long enough for the SIGKILL grace to land AND for the worker
    # thread to escape ``_filelock_with_telemetry``'s ``__exit__`` naturally
    # on the happy path. Default 5.0s matches HIVE_OUTBOX_TICK_S for symmetry.
    post_kill_drain_s: float = Field(default=5.0, ge=0.5, le=30.0)
    # HIVE-118 / ADR-011: how often the daemon polls its own installed package
    # version to detect an in-place upgrade (`uv tool upgrade`). On drift it
    # clean-stops and exits non-zero so the supervisor restarts into new code.
    upgrade_poll_s: float = Field(default=30.0, gt=0.0, le=3600.0)
    # Pass ``--no-verify`` on every write-path ``git commit``. Hive is
    # automation: its auto-commits should never fire the human-oriented
    # pre-commit hook chain. A slow vault hook (gitleaks + a language:python
    # local hook) takes ~9s warm and up to 60s cold/under concurrency, which
    # hung vault_write/vault_patch/capture_lesson for ~60s until the deadline
    # killed them. Secret scanning now lives push-side/CI on the vault, so
    # skipping the hook here is safe. Override with HIVE_GIT_COMMIT_NO_VERIFY.
    git_commit_no_verify: bool = True

    @model_validator(mode="after")
    def _worker_falls_back_to_embed(self) -> "HiveSettings":
        """Resolve unset ``worker_*`` from the ``embed_*`` values (HIVE-384).

        The worker is not an embedder, so it earns honest names — but a
        deployment commonly points both at one endpoint, and requiring both to
        be configured would mean a machine that works today stops working on
        upgrade. So the fallback is per field, not all-or-nothing: a deployment
        can override the model alone and inherit the endpoint.

        Fallback fills only what is *empty*; an explicit ``worker_*`` always
        wins. Both unset stays empty, which reads as "worker disabled" rather
        than as a guess.
        """
        if not self.worker_base_url:
            self.worker_base_url = self.embed_base_url
        if not self.worker_api_key:
            self.worker_api_key = self.embed_api_key
        if not self.worker_model:
            self.worker_model = self.embed_model
        return self

    @field_validator("vault_scopes")
    @classmethod
    def _validate_vault_scopes(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject scope mappings that would misbehave at runtime (#159).

        A clear startup error beats undefined behaviour later:

        - Two scopes sharing a directory — auto-scan is first-match, so the
          second key would be silently unreachable.
        - A directory that is absolute or climbs out of the vault root — a
          scope dir must be a relative path *inside* the vault. The per-call
          path-boundary check would catch this lazily; this fails loudly once.
        """
        dirs = list(value.values())
        duplicates = sorted({d for d in dirs if dirs.count(d) > 1})
        if duplicates:
            raise ValueError(
                f"vault_scopes maps multiple scopes to the same directory: {duplicates}",
            )
        for key, raw in value.items():
            is_absolute = raw.startswith(("/", "\\")) or bool(_DRIVE_RE.match(raw))
            segments = re.split(r"[\\/]", raw)
            if not raw.strip() or is_absolute or ".." in segments:
                raise ValueError(
                    f"vault_scopes[{key!r}] must be a relative path inside the vault, got {raw!r}",
                )
        return value


settings = HiveSettings()
