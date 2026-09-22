"""Worker process: connects to Temporal and serves the fix loop.

Everything about retries lives here and nowhere else. The Anthropic client runs
with its own retries disabled, so these policies are the only retry layer in the
system - which is what makes one history entry equal one billed call.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from control_plane import config

logger = logging.getLogger(__name__)


async def connect() -> Client:
    """Client with the pydantic converter, so domain models serialize as themselves."""
    return await Client.connect(
        config.TEMPORAL_TARGET,
        data_converter=pydantic_data_converter,
    )


async def run() -> None:
    # Imported here rather than at module scope so this module stays importable
    # while the workflow and activities are still being built out.
    from control_plane.activities import (
        analyze,
        branch,
        clone,
        patch,
        propose,
        pull_request,
        test_runner,
    )
    from control_plane.workflows.fix_workflow import FixWorkflow

    client = await connect()
    worker = Worker(
        client,
        task_queue=config.TASK_QUEUE,
        workflows=[FixWorkflow],
        activities=[
            test_runner.run_tests,
            analyze.analyze_repo,
            propose.propose_fix,
            patch.apply_patch,
            branch.create_fix_branch,
            clone.ensure_clone,
            pull_request.open_pr,
        ],
    )
    logger.info("worker listening on task queue %s", config.TASK_QUEUE)
    await worker.run()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
