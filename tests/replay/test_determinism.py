"""Replay is what makes resume possible (SC-008, Principle IV).

Temporal rebuilds a running workflow by replaying its event history against the
current code. If the code takes a different path than it did when the history
was recorded, the replay fails - and so would a real resume, at the worst
possible moment. This test moves that failure to CI.

Regenerate the history with `python scripts/capture_history.py` when the
workflow's activity sequence changes on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from control_plane.workflows.fix_workflow import FixWorkflow

HISTORY = Path(__file__).resolve().parent / "histories" / "fix_workflow.json"


@pytest.fixture
def recorded() -> WorkflowHistory:
    assert HISTORY.exists(), (
        f"{HISTORY.name} is missing - run scripts/capture_history.py to record it"
    )
    return WorkflowHistory.from_json("fix-workflow", json.loads(HISTORY.read_text()))


async def test_the_recorded_history_replays_against_the_current_workflow(recorded):
    replayer = Replayer(workflows=[FixWorkflow], data_converter=pydantic_data_converter)
    await replayer.replay_workflow(recorded)


async def test_the_history_covers_the_full_loop(recorded):
    """A history that stopped after one attempt would make the test above weak."""
    completed = sum(
        1
        for event in recorded.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
    )
    # ensure_clone + baseline + analyze + (propose, apply, run_tests) x 3
    # + create_fix_branch + open_pr
    assert completed == 14, f"expected 14 completed activities, recorded {completed}"


async def test_propose_fix_appears_once_per_attempt(recorded):
    """The accounting behind SC-003, asserted on a real history rather than a mock."""
    scheduled = [
        event
        for event in recorded.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]
    proposals = [
        e
        for e in scheduled
        if e.activity_task_scheduled_event_attributes.activity_type.name == "propose_fix"
    ]
    assert len(proposals) == 3, "one proposal per attempt, never more"


async def test_the_history_covers_the_pull_request(recorded):
    """A history that stopped at the branch would pin less than it looks."""
    scheduled = {
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in recorded.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    }
    assert {"ensure_clone", "open_pr"} <= scheduled


async def test_the_history_is_a_completed_run(recorded):
    assert any(
        event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED
        for event in recorded.events
    )
