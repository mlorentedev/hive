"""The ``mcp`` / ``fastmcp`` bounds are the audited majors, and only those (#434).

Dependabot's ``ignore: semver-major`` rule does not stop requirement-range
widening: it let ``mcp`` widen past its audited major twice (#316, #369), and
nothing failed. Moving to another major is a deliberate act, so it has to edit
this test as well as ``pyproject.toml``.

Audited 2026-09-24: on mcp 2.x the dispatcher never answers a cancelled
request, so the respond-after-cancel crash the old ``_compat`` patch guarded
(python-sdk#2416) cannot happen. The floors keep 1.x out, because nothing
patches it any more.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

_AUDITED = {
    "mcp": SpecifierSet(">=2.2,<3"),
    "fastmcp": SpecifierSet(">=4,<5"),
}


def _declared() -> dict[str, SpecifierSet]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    reqs = (Requirement(line) for line in data["project"]["dependencies"])
    return {req.name: req.specifier for req in reqs}


@pytest.mark.parametrize("name", sorted(_AUDITED))
def test_declared_bounds_are_the_audited_major(name: str) -> None:
    declared = _declared()[name]
    assert declared == _AUDITED[name], (
        f"{name} is declared as {declared!s}, audited as {_AUDITED[name]!s}. "
        "Audit the new range, then update both."
    )


@pytest.mark.parametrize("name", sorted(_AUDITED))
def test_suite_runs_on_the_audited_major(name: str) -> None:
    """The tests exercise what users install, not a stale lock or a stray venv."""
    installed = version(name)
    assert installed in _AUDITED[name], (
        f"{name}=={installed} is outside the audited range {_AUDITED[name]!s}"
    )
