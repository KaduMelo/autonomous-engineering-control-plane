"""The validator: does this code pass its tests?

A pure function of (revision, diff). It builds its own workspace from a clean
checkout, runs the suite in the sandbox, and destroys the workspace. Nothing it
produces has to survive this process, which is what makes a resume on a fresh
worker indistinguishable from a first run.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from temporalio import activity

from control_plane import config
from control_plane.adapters import repo, sandbox
from control_plane.domain.models import RunTestsInput, TestResult


async def _heartbeat_forever(attempt: int) -> None:
    """Report liveness so a hung container is detected rather than waited on."""
    elapsed = 0.0
    while True:
        await asyncio.sleep(config.HEARTBEAT_INTERVAL_S)
        elapsed += config.HEARTBEAT_INTERVAL_S
        activity.heartbeat({"attempt": attempt, "elapsed_s": elapsed})


@activity.defn
async def run_tests(request: RunTestsInput) -> TestResult:
    """Validate `revision` with `diff` applied, or pristine when diff is None.

    Returns a TestResult for a passing or failing suite. Raises TestRunnerError
    for anything else - see adapters.sandbox.verdict for the exit-code table.
    """
    root = config.workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="run-", dir=root))

    beat: asyncio.Task[None] | None = None
    try:
        repo.materialize(repo.resolve_source(request.repo_path), request.revision, workspace)
        if request.diff is not None:
            repo.apply_diff(workspace, request.diff)

        if activity.in_activity():
            beat = asyncio.create_task(_heartbeat_forever(request.attempt))

        run = await sandbox.run_suite(workspace)
        return sandbox.verdict(run)
    finally:
        if beat is not None:
            beat.cancel()
        await sandbox.destroy_workspace(workspace)
