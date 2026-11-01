"""Invariants for the site build-and-deploy workflow (#477).

`pages.yml` used to run only on a push to master, so a pull request that changed
`site/` (every Dependabot bump of the site's npm group) showed every check green
without the site ever being built; a broken bump surfaced after merge, as a failed
Pages deploy. The same workflow now builds on pull requests too, so a PR is
checked by the exact job that later deploys.

Sharing one workflow between a PR check and a deploy is what these tests guard.
Each failure mode is silent: a shared concurrency group lets a PR push cancel an
in-flight deploy, a workflow-level `pages: write` hands the Pages token to every
PR build, and an unguarded deploy job publishes a branch. None of these shows up
as a red check, and there is no actionlint in CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pages.yml"

PR_GUARD = "github.event_name != 'pull_request'"


def _load() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{WORKFLOW} did not parse into a mapping"
    return loaded


def _triggers() -> dict[str, Any]:
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1).
    workflow = _load()
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def _job(name: str) -> dict[str, Any]:
    job = _load()["jobs"][name]
    assert isinstance(job, dict)
    return job


def test_pull_requests_that_touch_the_site_build_it() -> None:
    pull_request = _triggers()["pull_request"]
    assert "site/**" in pull_request["paths"], "a PR changing site/ must build it"
    assert ".github/workflows/pages.yml" in pull_request["paths"], (
        "a PR changing this workflow must exercise it, or the check is edited blind"
    )
    build = _job("build")
    assert "if" not in build, "a gated build job turns the PR check green without building"
    commands = [s.get("run") for s in build["steps"]]
    assert "npm ci" in commands
    assert "npm run build" in commands


def test_master_pushes_still_deploy() -> None:
    push = _triggers()["push"]
    assert push["branches"] == ["master"]
    assert "site/**" in push["paths"]
    assert ".github/workflows/pages.yml" in push["paths"], (
        "merging a change to this workflow must deploy, or its deploy job first "
        "runs untested on the next unrelated site bump"
    )


def test_a_pull_request_never_deploys() -> None:
    assert _job("deploy").get("if") == PR_GUARD, "a PR run must skip the deploy job"
    upload = [s for s in _job("build")["steps"] if "upload-pages-artifact" in s.get("uses", "")]
    assert len(upload) == 1
    assert upload[0].get("if") == PR_GUARD, "a PR run must not upload a Pages artifact"


def test_the_pages_token_is_scoped_to_the_deploy_job() -> None:
    assert _load().get("permissions", {}) in ({}, {"contents": "read"}), (
        "workflow-level pages/id-token write would reach every PR build"
    )
    assert _job("build")["permissions"] == {"contents": "read"}
    assert _job("deploy")["permissions"] == {"pages": "write", "id-token": "write"}


def test_a_pull_request_cannot_cancel_a_deploy() -> None:
    concurrency = _load()["concurrency"]
    group = concurrency["group"]
    assert group != "pages", "a single group lets a PR push cancel an in-flight deploy"
    assert "github.event.pull_request.number" in group, "PR runs key on their own number"
    assert "'pages'" in group, "deploys keep the original `pages` group"
    assert concurrency["cancel-in-progress"] is True


def test_the_link_check_is_wired_after_build_and_before_upload() -> None:
    # Starlight ships `](/configuration/)` unrewritten, so a link missing the
    # /hive base builds green and 404s on Pages (#519). The check must run on
    # every build, PR or deploy, and before the artifact is uploaded.
    steps = _job("build")["steps"]
    commands = [s.get("run") for s in steps]
    assert "npm run check:links" in commands, "the build must scan dist/ for broken links"
    check = commands.index("npm run check:links")
    assert check > commands.index("npm run build"), "the scan reads dist/, so it runs after build"
    assert "if" not in steps[check], "a gated link check lets a PR or a deploy skip it"
    upload = next(i for i, s in enumerate(steps) if "upload-pages-artifact" in s.get("uses", ""))
    assert check < upload, "a broken link must stop the deploy before the artifact is uploaded"


def test_the_build_takes_the_newest_node_of_its_major() -> None:
    # `node-version: 22` alone is satisfied by whatever 22.x the runner image has
    # cached. Astro 7.4.2 requires >=22.23.4 while runners had 22.23.3, so its
    # Dependabot bump failed the build (#532). `check-latest` resolves the newest
    # 22.x on every run, so a dependency's raised floor never outruns the image.
    setup = [s for s in _job("build")["steps"] if "actions/setup-node" in s.get("uses", "")]
    assert len(setup) == 1
    assert setup[0]["with"].get("check-latest") is True, "a cached patch can miss the floor"
