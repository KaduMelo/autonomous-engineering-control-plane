"""Applying the same change twice must converge (SC-006, Principle III).

The mechanism under test is that apply_patch always starts from a clean
checkout of the target revision, so a retry can never stack a diff on top of a
previous application.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from control_plane.activities.patch import apply_patch
from control_plane.domain.errors import PatchApplyError
from control_plane.domain.models import ApplyInput

FIX_DIFF = """\
--- a/src/calculator/pricing.py
+++ b/src/calculator/pricing.py
@@ -25,4 +25,4 @@ def apply_discount(price: float, percent: float) -> float:
 
     `percent` is a percentage (10 means ten percent), not a fraction.
     \"\"\"
-    return price - percent
+    return price * (1.0 - percent / 100.0)
"""


@pytest.fixture(scope="module")
def seed(tmp_path_factory) -> tuple[str, str]:
    root = Path(__file__).resolve().parents[2]
    dest = tmp_path_factory.mktemp("seed") / "repo"
    subprocess.run(
        [str(root / "scripts" / "seed_repo.sh"), str(dest)], check=True, capture_output=True
    )
    revision = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return str(dest), revision


async def test_applying_twice_yields_an_identical_tree(seed):
    repo_path, revision = seed
    payload = ApplyInput(repo_path=repo_path, revision=revision, diff=FIX_DIFF)
    env = ActivityEnvironment()

    first = await env.run(apply_patch, payload)
    second = await env.run(apply_patch, payload)

    assert first.tree_hash == second.tree_hash
    assert first.tree_hash is not None


async def test_the_applied_tree_differs_from_the_pristine_one(seed):
    """Sanity: the diff must actually change something, or the test above is vacuous."""
    from control_plane.adapters import repo

    repo_path, revision = seed
    applied = await ActivityEnvironment().run(
        apply_patch, ApplyInput(repo_path=repo_path, revision=revision, diff=FIX_DIFF)
    )
    assert applied.tree_hash != repo.tree_of(repo_path, revision)


async def test_a_malformed_diff_raises_rather_than_corrupting(seed):
    repo_path, revision = seed
    with pytest.raises(PatchApplyError):
        await ActivityEnvironment().run(
            apply_patch,
            ApplyInput(repo_path=repo_path, revision=revision, diff="this is not a diff"),
        )


async def test_an_empty_diff_raises(seed):
    repo_path, revision = seed
    with pytest.raises(PatchApplyError):
        await ActivityEnvironment().run(
            apply_patch, ApplyInput(repo_path=repo_path, revision=revision, diff="   \n")
        )


async def test_a_diff_against_a_different_revision_does_not_apply(seed):
    """A patch that no longer applies is a spent attempt, not a corrupted tree."""
    repo_path, revision = seed
    stale = FIX_DIFF.replace("return price - percent", "return price - percent_typo")
    with pytest.raises(PatchApplyError):
        await ActivityEnvironment().run(
            apply_patch, ApplyInput(repo_path=repo_path, revision=revision, diff=stale)
        )
