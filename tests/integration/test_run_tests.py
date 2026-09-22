"""The run_tests activity against the real seed repository and real Docker.

This is US1's independent test: point the validator at a red repository and a
green one, then break the environment and confirm the contract holds in both
directions.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from control_plane.activities.test_runner import run_tests
from control_plane.domain.errors import TestRunnerError
from control_plane.domain.models import RunTestsInput

pytestmark = pytest.mark.docker

requires_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")

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
    """A throwaway clone of the seed repository, plus its revision."""
    root = Path(__file__).resolve().parents[2]
    dest = tmp_path_factory.mktemp("seed-under-test")
    subprocess.run(
        [str(root / "scripts" / "seed_repo.sh"), str(dest / "repo")],
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(dest / "repo"), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return str(dest / "repo"), revision


@requires_docker
async def test_red_repository_returns_a_failing_result_not_an_error(seed):
    repo_path, revision = seed
    result = await ActivityEnvironment().run(
        run_tests, RunTestsInput(repo_path=repo_path, revision=revision)
    )
    assert result.passed is False
    assert result.failing_tests, "the failing node ids feed the next proposal"
    assert all("apply_discount" in node for node in result.failing_tests)


@requires_docker
async def test_the_fix_turns_the_suite_green(seed):
    repo_path, revision = seed
    result = await ActivityEnvironment().run(
        run_tests,
        RunTestsInput(repo_path=repo_path, revision=revision, diff=FIX_DIFF),
    )
    assert result.passed is True
    assert result.failing_tests == []


@requires_docker
async def test_a_missing_image_is_an_error_not_a_red_suite(seed, monkeypatch):
    """The environment being broken must never look like failing tests."""
    from control_plane import config

    repo_path, revision = seed
    monkeypatch.setattr(config, "SANDBOX_IMAGE", "fixloop-sandbox:does-not-exist")
    with pytest.raises(TestRunnerError):
        await ActivityEnvironment().run(
            run_tests, RunTestsInput(repo_path=repo_path, revision=revision)
        )


@requires_docker
async def test_the_workspace_is_destroyed_after_the_run(seed):
    from control_plane import config

    repo_path, revision = seed
    before = set(config.workspace_root().glob("run-*"))
    await ActivityEnvironment().run(
        run_tests, RunTestsInput(repo_path=repo_path, revision=revision)
    )
    assert set(config.workspace_root().glob("run-*")) == before
