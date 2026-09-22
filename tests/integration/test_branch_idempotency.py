"""Branch creation is a side effect, so it must be safe to re-run (SC-010, FR-020).

Three states, three behaviors: absent means create, present-with-the-same-patch
means no-op, present-with-different-content means conflict and never overwrite.
"""

from __future__ import annotations

import pytest
from temporalio.testing import ActivityEnvironment

from control_plane.activities.branch import create_fix_branch
from control_plane.activities.patch import apply_patch
from control_plane.adapters import repo
from control_plane.domain.errors import BranchConflictError
from control_plane.domain.models import ApplyInput, BranchInput


async def _applied(repo_path: str, revision: str, diff: str):
    return await ActivityEnvironment().run(
        apply_patch, ApplyInput(repo_path=repo_path, revision=revision, diff=diff)
    )


async def test_absent_branch_is_created(seed_repo, fix_diff):
    repo_path, revision = seed_repo
    change = await _applied(repo_path, revision, fix_diff)

    branch = await ActivityEnvironment().run(
        create_fix_branch, BranchInput(repo_path=repo_path, revision=revision, change=change)
    )

    assert branch.created is True
    assert branch.name == f"fix/{revision}"
    assert repo.resolve(repo_path, branch.name) == branch.commit


async def test_the_branch_commit_carries_the_fix(seed_repo, fix_diff):
    repo_path, revision = seed_repo
    change = await _applied(repo_path, revision, fix_diff)
    branch = await ActivityEnvironment().run(
        create_fix_branch, BranchInput(repo_path=repo_path, revision=revision, change=change)
    )
    assert repo.tree_of(repo_path, branch.commit) == change.tree_hash


async def test_recreating_with_the_same_patch_is_a_no_op(seed_repo, fix_diff):
    """This is the idempotency guarantee - a retry or a resume must land here."""
    repo_path, revision = seed_repo
    change = await _applied(repo_path, revision, fix_diff)
    payload = BranchInput(repo_path=repo_path, revision=revision, change=change)
    env = ActivityEnvironment()

    first = await env.run(create_fix_branch, payload)
    second = await env.run(create_fix_branch, payload)

    assert first.created is True
    assert second.created is False
    assert first.commit == second.commit, "exactly one branch, at exactly one commit"


async def test_a_divergent_existing_branch_conflicts_and_is_not_overwritten(
    seed_repo, fix_diff, other_diff
):
    repo_path, revision = seed_repo
    mine = await _applied(repo_path, revision, fix_diff)
    theirs = await _applied(repo_path, revision, other_diff)
    env = ActivityEnvironment()

    existing = await env.run(
        create_fix_branch, BranchInput(repo_path=repo_path, revision=revision, change=theirs)
    )
    with pytest.raises(BranchConflictError):
        await env.run(
            create_fix_branch, BranchInput(repo_path=repo_path, revision=revision, change=mine)
        )

    assert repo.resolve(repo_path, f"fix/{revision}") == existing.commit, (
        "the earlier result must survive"
    )


async def test_the_branch_name_is_a_deterministic_function_of_the_revision(seed_repo, fix_diff):
    repo_path, revision = seed_repo
    change = await _applied(repo_path, revision, fix_diff)
    branch = await ActivityEnvironment().run(
        create_fix_branch, BranchInput(repo_path=repo_path, revision=revision, change=change)
    )
    assert branch.name == f"fix/{revision}"
