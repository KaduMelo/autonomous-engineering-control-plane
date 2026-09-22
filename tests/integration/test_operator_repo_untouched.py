"""The control plane writes a ref and nothing else (SC-011, FR-019).

Whatever the operator had checked out, staged or modified must be exactly as
they left it after a run finishes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from temporalio.testing import ActivityEnvironment

from control_plane.activities.branch import create_fix_branch
from control_plane.activities.patch import apply_patch
from control_plane.adapters import repo
from control_plane.domain.models import ApplyInput, BranchInput


async def _run_through_branch(repo_path: str, revision: str, diff: str):
    env = ActivityEnvironment()
    change = await env.run(
        apply_patch, ApplyInput(repo_path=repo_path, revision=revision, diff=diff)
    )
    return await env.run(
        create_fix_branch, BranchInput(repo_path=repo_path, revision=revision, change=change)
    )


async def test_head_index_and_worktree_are_unchanged(seed_repo, fix_diff):
    repo_path, revision = seed_repo
    before = repo.head_state(repo_path)

    await _run_through_branch(repo_path, revision, fix_diff)

    assert repo.head_state(repo_path) == before


async def test_uncommitted_operator_edits_survive(seed_repo, fix_diff):
    """The operator may be mid-edit. Nothing here may touch their working tree."""
    repo_path, revision = seed_repo
    scratch = Path(repo_path) / "src" / "calculator" / "pricing.py"
    scratch.write_text(scratch.read_text() + "\n# operator was here\n")
    before_text = scratch.read_text()
    before_status = repo.head_state(repo_path)[2]

    await _run_through_branch(repo_path, revision, fix_diff)

    assert scratch.read_text() == before_text
    assert repo.head_state(repo_path)[2] == before_status


async def test_the_checked_out_branch_does_not_move(seed_repo, fix_diff):
    repo_path, revision = seed_repo
    before = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    branch = await _run_through_branch(repo_path, revision, fix_diff)

    after = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert after == before == "main"
    assert branch.name != before
