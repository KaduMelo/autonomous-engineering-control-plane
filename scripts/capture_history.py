#!/usr/bin/env python
"""Record a workflow event history for the determinism test.

Run this when the workflow's activity sequence legitimately changes. That it is
a deliberate step is the point: re-recording forces a decision every time the
shape of the workflow moves, instead of letting a determinism break slip through
as a passing test.

    python scripts/capture_history.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from temporalio.contrib.pydantic import pydantic_data_converter  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from control_plane.domain.models import FixRequest  # noqa: E402
from control_plane.workflows.fix_workflow import FixWorkflow  # noqa: E402
from tests.fakes import ALL, REVISION  # noqa: E402

DEST = ROOT / "tests" / "replay" / "histories" / "fix_workflow.json"


async def main() -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        queue = f"capture-{uuid.uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[FixWorkflow], activities=ALL
        ):
            workflow_id = f"fix-{REVISION}"
            handle = env.client.get_workflow_handle(workflow_id)
            outcome = await env.client.execute_workflow(
                FixWorkflow.run,
                FixRequest(repo_path="/seed", revision=REVISION, max_attempts=3),
                id=workflow_id,
                task_queue=queue,
            )
            history = await handle.fetch_history()

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(json.loads(history.to_json()), indent=2, sort_keys=True) + "\n")
    print(f"outcome: {outcome.status} after {len(outcome.attempts)} attempts")
    print(f"written: {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
