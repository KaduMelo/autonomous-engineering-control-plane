"""open_pr against a real bare remote and the runnable host stand-in (T023).

Exactly one pull request, however many times the step runs (SC-004), and none at
all for runs that did not reach green (SC-005).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx2
import pytest
from temporalio.testing import ActivityEnvironment

from control_plane.activities.branch import create_fix_branch
from control_plane.activities.patch import apply_patch
from control_plane.activities.pull_request import open_pr, set_host
from control_plane.adapters import host as host_module
from control_plane.adapters import repo
from control_plane.domain.models import ApplyInput, BranchInput, OpenPullRequestInput

FULL_NAME = "owner/name"


def _load_fake_host():
    """Import the stand-in as it ships, rather than reimplementing it here.

    A fake written twice is a fake that can disagree with itself.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "fake_host.py"
    spec = importlib.util.spec_from_file_location("fake_host", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stand_in():
    module = _load_fake_host()
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=module.app), base_url="http://host.invalid"
    )
    set_host(host_module.GitHubHost(base_url="http://host.invalid", token="t", client=client))
    yield module
    set_host(None)


@pytest.fixture
async def pushed_branch(seed_repo, bare_remote, fix_diff, stand_in):
    """A repository with a fix branch and an origin to push it to."""
    repo_path, revision = seed_repo
    repo.set_remote(repo_path, "origin", bare_remote)

    env = ActivityEnvironment()
    change = await env.run(
        apply_patch, ApplyInput(repo_path=repo_path, revision=revision, diff=fix_diff)
    )
    branch = await env.run(
        create_fix_branch,
        BranchInput(repo_path=repo_path, revision=revision, change=change),
    )
    return repo_path, revision, branch, change


def _request(repo_path, revision, branch, change, attempts=3) -> OpenPullRequestInput:
    return OpenPullRequestInput(
        source=repo_path,
        repository_full_name=FULL_NAME,
        revision=revision,
        branch=branch.name,
        failing_tests=["tests/test_pricing.py::test_apply_discount"],
        winning_diff=change.diff,
        rationale="percent is a percentage of the price",
        attempts=attempts,
    )


async def test_a_green_run_publishes_the_branch_and_opens_one_pull_request(
    pushed_branch, bare_remote
):
    repo_path, revision, branch, change = pushed_branch

    ref = await ActivityEnvironment().run(open_pr, _request(repo_path, revision, branch, change))

    assert ref.created is True
    assert repo.resolve(bare_remote, branch.name) == branch.commit, (
        "the branch must actually be on the remote"
    )


async def test_running_it_twice_still_leaves_exactly_one(pushed_branch):
    """SC-004. The second call finds the first rather than making another."""
    repo_path, revision, branch, change = pushed_branch
    payload = _request(repo_path, revision, branch, change)
    env = ActivityEnvironment()

    first = await env.run(open_pr, payload)
    second = await env.run(open_pr, payload)

    assert first.number == second.number
    assert first.created is True
    assert second.created is False


async def test_ten_runs_still_leave_exactly_one(pushed_branch, stand_in):
    repo_path, revision, branch, change = pushed_branch
    payload = _request(repo_path, revision, branch, change)
    env = ActivityEnvironment()

    refs = [await env.run(open_pr, payload) for _ in range(10)]

    assert len({ref.number for ref in refs}) == 1
    assert sum(1 for ref in refs if ref.created) == 1


async def test_the_pull_request_carries_the_evidence(pushed_branch, stand_in):
    repo_path, revision, branch, change = pushed_branch
    await ActivityEnvironment().run(open_pr, _request(repo_path, revision, branch, change))

    stored = next(iter(stand_in._STATE[FULL_NAME].values()))
    assert revision[:12] in stored["title"]
    assert "test_apply_discount" in stored["body"]
    assert "```diff" in stored["body"]
    assert "attempt **3**" in stored["body"]


async def test_the_body_warns_that_a_green_suite_is_not_proof(pushed_branch, stand_in):
    """FR-018's mitigation, where the reviewer actually looks."""
    repo_path, revision, branch, change = pushed_branch
    await ActivityEnvironment().run(open_pr, _request(repo_path, revision, branch, change))

    stored = next(iter(stand_in._STATE[FULL_NAME].values()))
    assert "not proof" in stored["body"].lower()


async def test_a_push_to_a_missing_remote_raises(seed_repo, stand_in, fix_diff):
    """An unreachable remote is infrastructure: an error, not a fix that failed."""
    repo_path, revision = seed_repo
    repo.set_remote(repo_path, "origin", "/nonexistent/remote.git")

    env = ActivityEnvironment()
    change = await env.run(
        apply_patch, ApplyInput(repo_path=repo_path, revision=revision, diff=fix_diff)
    )
    branch = await env.run(
        create_fix_branch,
        BranchInput(repo_path=repo_path, revision=revision, change=change),
    )

    with pytest.raises(Exception, match="could not push"):
        await env.run(open_pr, _request(repo_path, revision, branch, change))
