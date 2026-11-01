"""The docs-site link checker fails on the links that 404 on GitHub Pages (#519).

`tests/test_pages_workflow.py` pins where `npm run check:links` runs; these tests run
the script itself against a fixture `dist/`, so a checker that always exits 0 goes red
here instead of guarding nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "site" / "scripts" / "check-links.mjs"

NODE = shutil.which("node")
if NODE is None:
    # Skipping in CI would leave the checker untested exactly where it matters.
    if os.environ.get("CI"):
        pytest.fail("node is required to test site/scripts/check-links.mjs", pytrace=False)
    pytest.skip("node is not installed", allow_module_level=True)


def _page(dist: Path, rel: str, body: str) -> None:
    path = dist / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!doctype html><html><body>{body}</body></html>", encoding="utf-8")


def _check(dist: Path) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    return subprocess.run(
        [NODE, str(SCRIPT), str(dist)], capture_output=True, text=True, timeout=30, check=False
    )


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    _page(tmp_path, "index.html", '<a href="/hive/configuration/">ok</a>')
    _page(tmp_path, "configuration/index.html", '<img src="/hive/favicon.svg">')
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return tmp_path


def test_valid_links_pass(dist: Path) -> None:
    result = _check(dist)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "link",
    [
        '<a href="/configuration/">x</a>',
        "<a href='/configuration/'>x</a>",
        '<a href = "/configuration/">x</a>',
    ],
)
def test_a_link_missing_the_base_fails(dist: Path, link: str) -> None:
    _page(dist, "guides/index.html", link)
    result = _check(dist)
    assert result.returncode == 1
    assert "/configuration/ (missing /hive/ base)" in result.stderr


def test_a_link_to_a_page_that_was_not_built_fails(dist: Path) -> None:
    _page(dist, "guides/index.html", '<a href="/hive/no-such-page/#x">x</a>')
    result = _check(dist)
    assert result.returncode == 1
    assert "/hive/no-such-page/#x (no such page)" in result.stderr


def test_a_directory_without_an_index_is_not_a_page(dist: Path) -> None:
    _page(dist, "guides/child/index.html", "")
    _page(dist, "index.html", '<a href="/hive/guides/">x</a>')
    result = _check(dist)
    assert result.returncode == 1
    assert "/hive/guides/ (no such page)" in result.stderr


def test_external_and_protocol_relative_links_are_ignored(dist: Path) -> None:
    _page(dist, "guides/index.html", '<a href="https://x.org/">x</a><a href="//cdn.x/a.js">y</a>')
    result = _check(dist)
    assert result.returncode == 0, result.stderr
