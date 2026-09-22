"""Start a run.

The workflow id is derived from the revision, so the engine itself rejects a
second concurrent run for the same commit - the same mechanism the later
webhook phase will lean on, rather than a hand-rolled dedup table.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from control_plane import config
from control_plane.domain.models import FixRequest, RunOutcome
from control_plane.worker import connect
from control_plane.workflows.fix_workflow import FixWorkflow


def _resolve_revision(repo_path: str, revision: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--verify", f"{revision}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"revision {revision!r} not found in {repo_path}")
    return proc.stdout.strip()


async def _run(repo_path: str, revision: str, max_attempts: int, as_json: bool) -> int:
    client = await connect()
    workflow_id = f"fix-{revision}"

    outcome: RunOutcome = await client.execute_workflow(
        FixWorkflow.run,
        FixRequest(repo_path=repo_path, revision=revision, max_attempts=max_attempts),
        id=workflow_id,
        task_queue=config.TASK_QUEUE,
    )

    if as_json:
        print(outcome.model_dump_json(indent=2))
    else:
        print(f"workflow   {workflow_id}")
        print(f"status     {outcome.status}")
        print(f"attempts   {len(outcome.attempts)}")
        for attempt in outcome.attempts:
            verdict = (
                "patch did not apply"
                if not attempt.applied
                else ("green" if attempt.result and attempt.result.passed else "still red")
            )
            print(f"  {attempt.ordinal}. {verdict} - {attempt.change.rationale[:70]}")
        if outcome.branch:
            print(f"branch     {outcome.branch.name} @ {outcome.branch.commit[:12]}")
        if outcome.status == "conflicted":
            print("the fix branch already exists with different content; nothing was overwritten")
    return 0 if outcome.status == "fixed" else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="fixctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fix a repository at a revision")
    run.add_argument("--repo", required=True, help="path to the target repository")
    run.add_argument("--sha", default="HEAD", help="revision to fix (default: HEAD)")
    run.add_argument("--max-attempts", type=int, default=config.DEFAULT_MAX_ATTEMPTS)
    run.add_argument("--json", action="store_true", help="print the outcome as JSON")

    args = parser.parse_args()
    repo_path = str(Path(args.repo).resolve())
    revision = _resolve_revision(repo_path, args.sha)
    raise SystemExit(asyncio.run(_run(repo_path, revision, args.max_attempts, args.json)))


if __name__ == "__main__":
    main()
