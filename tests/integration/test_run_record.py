"""A finished run must explain itself from its own record (US4, FR-013, SC-009).

The point is auditability. FR-018 lets the fixer touch test files, so a green
verdict is not proof on its own - the diff that produced it has to survive in
the record for a human to read. These tests pin that.
"""

from __future__ import annotations

import uuid

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from control_plane.domain.errors import BranchConflictError, PatchApplyError
from control_plane.domain.models import (
    AnalyzeInput,
    ApplyInput,
    BranchInput,
    CloneInput,
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

REVISION = "b" * 40

# One entry per attempt: "apply_fail" | "red" | "green"
plan: list[str] = []
branch_mode = "created"


@activity.defn(name="run_tests")
async def run_tests(request: RunTestsInput) -> TestResult:
    if request.attempt == 0:
        return TestResult(
            passed=False,
            output="3 failed, 4 passed",
            failing_tests=["tests/test_pricing.py::test_apply_discount"],
        )
    passed = plan[request.attempt - 1] == "green"
    return TestResult(
        passed=passed,
        output=f"attempt {request.attempt} output",
        failing_tests=[] if passed else ["tests/test_pricing.py::test_apply_discount"],
    )


@activity.defn(name="analyze_repo")
async def analyze_repo(request: AnalyzeInput) -> RepositoryContext:
    return RepositoryContext(
        failing_tests=list(request.baseline.failing_tests),
        failure_output=request.baseline.output,
        sources=[SourceFile(path="src/calculator/pricing.py", content="...")],
    )


@activity.defn(name="propose_fix")
async def propose_fix(request: ProposeInput) -> ProposedChange:
    ordinal = len(request.history) + 1
    return ProposedChange(
        diff=f"--- a/x\n+++ b/x\n@@\n-old-{ordinal}\n+new-{ordinal}\n",
        rationale=f"reasoning for attempt {ordinal}",
    )


@activity.defn(name="apply_patch")
async def apply_patch(request: ApplyInput) -> ProposedChange:
    ordinal = int(request.diff.split("new-")[1].split("\n")[0])
    if plan[ordinal - 1] == "apply_fail":
        raise PatchApplyError("diff did not apply")
    return ProposedChange(diff=request.diff, rationale="", tree_hash=f"tree-{ordinal}")


@activity.defn(name="create_fix_branch")
async def create_fix_branch(request: BranchInput) -> FixBranch:
    if branch_mode == "conflict":
        raise BranchConflictError("already exists with different content")
    return FixBranch(name=f"fix/{request.revision}", commit="c" * 40, created=True)


@activity.defn(name="ensure_clone")
async def fake_ensure_clone(request: CloneInput) -> str:
    """The repository is a given in these tests; obtaining it is not what they check."""
    return "cache-key"


@pytest.fixture
async def run():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        queue = f"record-{uuid.uuid4().hex}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[FixWorkflow],
            activities=[
                fake_ensure_clone,
                run_tests,
                analyze_repo,
                propose_fix,
                apply_patch,
                create_fix_branch,
            ],
        ):

            async def go(outcomes: list[str], branch: str = "created", max_attempts: int = 3):
                global plan, branch_mode
                plan = outcomes
                branch_mode = branch
                return await env.client.execute_workflow(
                    FixWorkflow.run,
                    FixRequest(repo_path="/seed", revision=REVISION, max_attempts=max_attempts),
                    id=f"fix-{uuid.uuid4().hex}",
                    task_queue=queue,
                )

            yield go


async def test_the_record_reconstructs_the_narrative_in_order(run):
    outcome = await run(["apply_fail", "red", "green"])

    story = [
        (a.ordinal, a.applied, None if a.result is None else a.result.passed)
        for a in outcome.attempts
    ]
    assert story == [(1, False, None), (2, True, False), (3, True, True)]


async def test_every_attempt_carries_the_diff_and_the_reasoning(run):
    """SC-009: a green verdict is audited against what actually changed."""
    outcome = await run(["red", "green"])

    for attempt in outcome.attempts:
        assert attempt.change.diff, f"attempt {attempt.ordinal} lost its diff"
        assert attempt.change.rationale, f"attempt {attempt.ordinal} lost its reasoning"


async def test_a_rejected_patch_still_records_what_was_proposed(run):
    """The attempt that never ran is the one most worth reading."""
    outcome = await run(["apply_fail", "green"])

    rejected = outcome.attempts[0]
    assert rejected.applied is False
    assert rejected.result is None
    assert "new-1" in rejected.change.diff
    assert rejected.change.rationale == "reasoning for attempt 1"


async def test_the_winning_change_is_reachable_without_scanning_attempts(run):
    outcome = await run(["red", "green"])

    assert outcome.winning_change is not None
    assert outcome.winning_change.diff == outcome.attempts[-1].change.diff
    assert outcome.winning_change.tree_hash is not None


async def test_an_exhausted_run_states_its_terminal_reason(run):
    outcome = await run(["red", "red", "red"], max_attempts=3)

    assert outcome.status == "exhausted"
    assert len(outcome.attempts) == 3
    assert outcome.winning_change is None
    assert outcome.branch is None


async def test_a_conflicted_run_preserves_the_validated_patch(run):
    outcome = await run(["green"], branch="conflict")

    assert outcome.status == "conflicted"
    assert outcome.winning_change is not None
    assert outcome.winning_change.tree_hash == "tree-1"


async def test_the_failure_output_of_each_attempt_is_kept(run):
    outcome = await run(["red", "red", "green"])

    reds = [a for a in outcome.attempts if a.result and not a.result.passed]
    assert [a.result.output for a in reds] == ["attempt 1 output", "attempt 2 output"]
