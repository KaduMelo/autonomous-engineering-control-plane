"""The loop's behavior, driven by scripted activities.

Real Docker and a real model are not what is under test here - the control flow
is. Every activity is replaced by a fake with the same registered name, so the
workflow runs exactly as it would in production while the test decides what each
step returns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from control_plane.domain.errors import BranchConflictError, PatchApplyError
from control_plane.domain.models import (
    AnalyzeInput,
    ApplyInput,
    BranchInput,
    FixBranch,
    FixRequest,
    ProposedChange,
    ProposeInput,
    RepositoryContext,
    RunTestsInput,
    SourceFile,
    TestResult,
)
from control_plane.workflows.fix_workflow import FixWorkflow

REVISION = "a" * 40


@dataclass
class Script:
    """What the fakes should do, per step."""

    baseline_passed: bool = False
    # One entry per attempt: "apply_fail" | "red" | "green"
    attempts: list[str] = field(default_factory=list)
    branch: str = "created"  # "created" | "noop" | "conflict"
    propose_calls: int = 0
    run_tests_calls: int = 0


script = Script()


@activity.defn(name="run_tests")
async def fake_run_tests(request: RunTestsInput) -> TestResult:
    script.run_tests_calls += 1
    if request.attempt == 0:
        return TestResult(
            passed=script.baseline_passed,
            output="baseline",
            failing_tests=[] if script.baseline_passed else ["tests/test_pricing.py::test_x"],
        )
    outcome = script.attempts[request.attempt - 1]
    return TestResult(passed=outcome == "green", output=f"attempt {request.attempt}")


@activity.defn(name="analyze_repo")
async def fake_analyze_repo(request: AnalyzeInput) -> RepositoryContext:
    return RepositoryContext(
        failing_tests=list(request.baseline.failing_tests),
        failure_output=request.baseline.output,
        sources=[SourceFile(path="src/calculator/pricing.py", content="...")],
    )


@activity.defn(name="propose_fix")
async def fake_propose_fix(request: ProposeInput) -> ProposedChange:
    script.propose_calls += 1
    return ProposedChange(diff=f"diff-{script.propose_calls}", rationale="because")


@activity.defn(name="apply_patch")
async def fake_apply_patch(request: ApplyInput) -> ProposedChange:
    index = int(request.diff.split("-")[1]) - 1
    if script.attempts[index] == "apply_fail":
        raise PatchApplyError("diff did not apply")
    return ProposedChange(diff=request.diff, rationale="", tree_hash=f"tree-{index}")


@activity.defn(name="create_fix_branch")
async def fake_create_fix_branch(request: BranchInput) -> FixBranch:
    if script.branch == "conflict":
        raise BranchConflictError("already exists with different content")
    return FixBranch(
        name=f"fix/{request.revision}",
        commit="c" * 40,
        created=script.branch == "created",
    )


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as environment:
        yield environment


async def _run(client: Client, task_queue: str, max_attempts: int = 3):
    return await client.execute_workflow(
        FixWorkflow.run,
        FixRequest(repo_path="/seed", revision=REVISION, max_attempts=max_attempts),
        id=f"fix-{uuid.uuid4().hex}",
        task_queue=task_queue,
    )


@pytest.fixture
async def run(env):
    """Start a worker with the fakes and hand back a runner."""
    queue = f"test-{uuid.uuid4().hex}"
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[FixWorkflow],
        activities=[
            fake_run_tests,
            fake_analyze_repo,
            fake_propose_fix,
            fake_apply_patch,
            fake_create_fix_branch,
        ],
    ):

        async def go(max_attempts: int = 3):
            return await _run(env.client, queue, max_attempts)

        yield go


@pytest.fixture(autouse=True)
def reset_script():
    global script
    script = Script()
    import tests.integration.test_fix_workflow_loop as module

    module.script = script
    yield


async def test_an_already_green_repository_ends_with_zero_attempts(run):
    """FR-017: do not propose a change to working code."""
    script.baseline_passed = True

    outcome = await run()

    assert outcome.status == "fixed"
    assert outcome.attempts == []
    assert script.propose_calls == 0, "no proposal should be billed for a green repository"


async def test_the_loop_stops_at_the_first_green_verdict(run):
    script.attempts = ["red", "green", "red"]

    outcome = await run(max_attempts=3)

    assert outcome.status == "fixed"
    assert len(outcome.attempts) == 2
    assert script.propose_calls == 2, "the third attempt must not be proposed"
    assert outcome.branch is not None
    assert outcome.winning_change is not None


async def test_the_attempt_budget_is_never_exceeded(run):
    script.attempts = ["red", "red", "red"]

    outcome = await run(max_attempts=3)

    assert outcome.status == "exhausted"
    assert len(outcome.attempts) == 3
    assert script.propose_calls == 3
    assert outcome.branch is None


async def test_a_smaller_budget_is_honoured(run):
    script.attempts = ["red", "red", "red"]

    outcome = await run(max_attempts=2)

    assert outcome.status == "exhausted"
    assert script.propose_calls == 2


async def test_a_diff_that_does_not_apply_spends_the_attempt_and_continues(run):
    script.attempts = ["apply_fail", "green"]

    outcome = await run(max_attempts=3)

    assert outcome.status == "fixed"
    assert outcome.attempts[0].applied is False
    assert outcome.attempts[0].result is None
    assert outcome.attempts[1].applied is True


async def test_every_attempt_is_recorded_in_order(run):
    script.attempts = ["apply_fail", "red", "green"]

    outcome = await run(max_attempts=3)

    assert [a.ordinal for a in outcome.attempts] == [1, 2, 3]
    assert [a.applied for a in outcome.attempts] == [False, True, True]


async def test_one_proposal_per_attempt_and_no_more(run):
    """The accounting SC-003 depends on: one history entry, one billed call."""
    script.attempts = ["red", "red", "green"]

    outcome = await run(max_attempts=3)

    assert script.propose_calls == len(outcome.attempts) == 3


async def test_a_branch_conflict_preserves_the_fix_and_overwrites_nothing(run):
    script.attempts = ["green"]
    script.branch = "conflict"

    outcome = await run(max_attempts=3)

    assert outcome.status == "conflicted"
    assert outcome.winning_change is not None, "the validated patch must survive"
    assert outcome.branch is None


async def test_an_existing_identical_branch_is_a_no_op_success(run):
    script.attempts = ["green"]
    script.branch = "noop"

    outcome = await run(max_attempts=3)

    assert outcome.status == "fixed"
    assert outcome.branch is not None
    assert outcome.branch.created is False


async def test_the_baseline_runs_before_any_proposal(run):
    script.attempts = ["green"]

    await run()

    # baseline + one attempt
    assert script.run_tests_calls == 2
