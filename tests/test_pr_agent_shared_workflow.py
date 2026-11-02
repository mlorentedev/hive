"""The caller of the shared reviewer (TOOL-031, dotfiles#2893).

A tag or a branch here would let a change in ci-workflows reach this repository
without a commit in it, so the pin is held to a full SHA with its tag beside it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pr-agent-shared.yml"
PINNED_LINE = re.compile(
    r"uses: mlorentedev/ci-workflows/\.github/workflows/pr-agent\.yml"
    r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$",
    re.MULTILINE,
)


def _job() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["review"]


def test_the_shared_workflow_is_pinned_by_full_sha_and_tag() -> None:
    assert PINNED_LINE.search(WORKFLOW.read_text(encoding="utf-8"))


def test_the_pool_comes_from_the_dotf_pin() -> None:
    assert _job()["with"]["pool"] == "embedded"


def test_dotf_is_pinned_to_a_release() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(_job()["with"]["dotf_version"]))


def test_secrets_are_passed_by_name() -> None:
    assert set(_job()["secrets"]) == {"NAN_API_KEY", "PR_AGENT_ANTHROPIC_API_KEY"}
