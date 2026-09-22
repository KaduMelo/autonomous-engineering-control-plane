"""A fixed scenario used to record a workflow history.

Deliberately not configurable: the recorded history must be the same every time
it is regenerated, or the determinism test would drift along with the code it is
supposed to pin.

The scenario is the one the kill-resume demo mirrors - two red attempts, then a
green one - so the replay test exercises the same shape as the headline demo.
"""

from __future__ import annotations

from temporalio import activity

from control_plane.domain.models import (
    AnalyzeInput,
    ApplyInput,
    BranchInput,
    FixBranch,
    ProposedChange,
    ProposeInput,
    RepositoryContext,
    RunTestsInput,
    SourceFile,
    TestResult,
)

REVISION = "4b1a0d431c4eda26a485496e3f50504dd49d7f2f"
OUTCOMES = ["red", "red", "green"]


@activity.defn(name="run_tests")
async def run_tests(request: RunTestsInput) -> TestResult:
    if request.attempt == 0:
        return TestResult(
            passed=False,
            output="3 failed, 4 passed",
            failing_tests=["tests/test_pricing.py::test_apply_discount"],
        )
    passed = OUTCOMES[request.attempt - 1] == "green"
    return TestResult(passed=passed, output=f"attempt {request.attempt}")


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
    return ProposedChange(diff=f"diff-{ordinal}", rationale=f"attempt {ordinal}")


@activity.defn(name="apply_patch")
async def apply_patch(request: ApplyInput) -> ProposedChange:
    return ProposedChange(diff=request.diff, rationale="", tree_hash=f"tree-{request.diff}")


@activity.defn(name="create_fix_branch")
async def create_fix_branch(request: BranchInput) -> FixBranch:
    return FixBranch(name=f"fix/{request.revision}", commit="c" * 40, created=True)


ALL = [run_tests, analyze_repo, propose_fix, apply_patch, create_fix_branch]
