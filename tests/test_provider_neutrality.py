"""Hive names no provider.

``hive-vault`` is published. Whoever installs it points the worker and the
semantic backend at whatever OpenAI-compatible endpoint they run, and the tool
must describe itself in those terms — not in the terms of the deployment it
happened to be developed against.

Two failure shapes are guarded here, because they leak in opposite directions:

* a **label** the tool asserts (``provider_name="<brand>"``) states a fact it
  does not know, and reaches users through every error message;
* an **alias** the tool accepts (``NAN_API_KEY``) makes a public package read a
  variable that only means something inside one private deployment.

Both shipped in 4.0.0. See ``mlorentedev/hive#391``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hive.clients import OpenAICompatibleClient, provider_label
from hive.config import HiveSettings

# Modules that configure, construct or describe a provider client. A brand name
# in any of these is the defect this file exists to catch.
_PROVIDER_FACING_MODULES = (
    "clients.py",
    "config.py",
    "server.py",
    "_context.py",
    "_vault_ask.py",
)

# ``_workers.py`` is deliberately absent: it names the retired model aliases
# (``auto`` / ``ollama`` / ``openrouter-free`` / ``openrouter``) because
# *rejecting them by name* is its behaviour. A guard that forbade the words
# there would forbid the feature.
_BRANDS = re.compile(r"\bNaN\b|\bOllama\b|\bOpenRouter\b|NAN_API_KEY|X-OpenRouter", re.IGNORECASE)


def _src_dir() -> Path:
    import hive

    return Path(hive.__file__).parent


class TestNoBrandInProviderFacingModules:
    @pytest.mark.parametrize("module", _PROVIDER_FACING_MODULES)
    def test_module_names_no_specific_provider(self, module: str) -> None:
        path = _src_dir() / module
        offenders = [
            f"{module}:{n}: {line.strip()}"
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _BRANDS.search(line)
        ]
        assert not offenders, (
            "a provider brand is named in a module that configures or describes "
            "a client; hive supports any OpenAI-compatible endpoint and must not "
            "single one out:\n  " + "\n  ".join(offenders)
        )


class TestProviderLabelIsDerived:
    """The label cannot disagree with the endpoint, because it comes from it."""

    @pytest.mark.parametrize(
        ("base_url", "expected"),
        [
            ("https://api.example.com/v1", "api.example.com"),
            ("http://localhost:11434/v1", "localhost"),
            ("https://Inference.Example.COM/v1", "inference.example.com"),
            ("https://api.example.com:8443/v1", "api.example.com"),
        ],
    )
    def test_label_is_the_host(self, base_url: str, expected: str) -> None:
        assert provider_label(base_url) == expected

    @pytest.mark.parametrize("base_url", ["", "not a url", "/v1"])
    def test_unparseable_base_url_still_labels_something(self, base_url: str) -> None:
        """An empty prefix would render errors as ' unavailable: ...'."""
        assert provider_label(base_url) == "OpenAI-compatible endpoint"

    def test_client_defaults_its_label_to_the_host(self) -> None:
        client = OpenAICompatibleClient(base_url="https://api.example.com/v1")
        assert client._provider_name == "api.example.com"

    def test_an_explicit_role_label_still_wins(self) -> None:
        """``embed`` / ``synth`` name which client failed, not which host."""
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1", provider_name="embed"
        )
        assert client._provider_name == "embed"

    def test_no_provider_specific_request_header(self) -> None:
        client = OpenAICompatibleClient(base_url="https://api.example.com/v1", api_key="k")
        assert set(client._http.headers) >= {"authorization"}
        assert not [h for h in client._http.headers if _BRANDS.search(h)]


class TestNoLauncherAliasForTheCredential:
    def test_nan_api_key_does_not_populate_the_worker_credential(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mapping a launcher's own name is the launcher's job, at injection.

        Removed rather than deprecated: the alias shipped in 4.0.0 and had no
        possible consumer, because a worker only constructs when
        ``HIVE_WORKER_BASE_URL`` is set and no deployment set it.
        """
        monkeypatch.delenv("HIVE_WORKER_API_KEY", raising=False)
        monkeypatch.setenv("NAN_API_KEY", "from-a-launcher")
        assert HiveSettings().worker_api_key == ""

    def test_the_documented_name_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIVE_WORKER_API_KEY", "the-documented-name")
        assert HiveSettings().worker_api_key == "the-documented-name"
