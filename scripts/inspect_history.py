#!/usr/bin/env python
"""Read a run's event history - the system of record for what actually happened.

    python scripts/inspect_history.py fix-<sha>

Prints the attempt-by-attempt narrative and, most importantly, how many times
propose_fix was scheduled. That count is SC-003: after a worker is killed and
replaced, it must still equal the number of attempts, with no duplicates for
attempts that had already finished.

The count is only meaningful because the Anthropic client runs with
max_retries=0 - one scheduled activity is one billed call.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporalio.api.enums.v1 import EventType  # noqa: E402

from control_plane.worker import connect  # noqa: E402

SCHEDULED = EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
STARTED = EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED
COMPLETED = EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED


async def count_scheduled(
    workflow_id: str, activity_name: str, run_id: str | None = None
) -> int:
    """Just the number, for scripts. Scraping the human report is too brittle."""
    client = await connect()
    history = await client.get_workflow_handle(workflow_id, run_id=run_id).fetch_history()
    return sum(
        1
        for event in history.events
        if event.event_type == SCHEDULED
        and event.activity_task_scheduled_event_attributes.activity_type.name == activity_name
    )


async def main(workflow_id: str, run_id: str | None = None) -> int:
    client = await connect()
    handle = client.get_workflow_handle(workflow_id, run_id=run_id)
    history = await handle.fetch_history()

    scheduled: Counter[str] = Counter()
    completed = 0
    workers: set[str] = set()
    for event in history.events:
        if event.event_type == SCHEDULED:
            scheduled[event.activity_task_scheduled_event_attributes.activity_type.name] += 1
        elif event.event_type == COMPLETED:
            completed += 1
        elif event.event_type == STARTED:
            identity = event.workflow_task_started_event_attributes.identity
            if identity:
                workers.add(identity)

    print(f"workflow            {workflow_id}")
    print(f"events              {len(history.events)}")
    print(f"activities done     {completed}")
    print("scheduled by type:")
    for name, count in sorted(scheduled.items()):
        print(f"  {name:20s} {count}")
    print()
    print(f"distinct workers    {len(workers)}")
    if len(workers) > 1:
        print("  -> the run was served by more than one worker: it survived a restart")
    print()
    print(f"SC-003: propose_fix scheduled {scheduled['propose_fix']} time(s).")
    print("  This must equal the number of attempts. Any excess means an attempt that")
    print("  had already finished was re-proposed - and re-billed - after the resume.")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    run_id = None
    if "--run-id" in argv:
        i = argv.index("--run-id")
        run_id = argv[i + 1]
        del argv[i : i + 2]
    if len(argv) == 3 and argv[1] == "--count":
        print(asyncio.run(count_scheduled(argv[0], argv[2], run_id)))
        raise SystemExit(0)
    if len(argv) != 1:
        sys.exit(__doc__)
    raise SystemExit(asyncio.run(main(argv[0], run_id)))
