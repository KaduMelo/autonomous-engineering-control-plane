"""The durable loop.

This module is the one place in the system that must be deterministic. It does
no I/O, reads no clock, generates no randomness, and imports nothing from
`adapters`. Its entire state is the local variables below, which Temporal
reconstructs by replaying the event history - which is exactly what makes a
resume on a fresh worker indistinguishable from a first run.
"""

from __future__ import annotations

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from control_plane import config
    from control_plane.activities.analyze import analyze_repo
    from control_plane.activities.branch import create_fix_branch
    from control_plane.activities.patch import apply_patch
    from control_plane.activities.propose import propose_fix
    from control_plane.activities.test_runner import run_tests
    from control_plane.domain.models import (
        AnalyzeInput,
        ApplyInput,
        Attempt,
        BranchInput,
        FixRequest,
        ProposedChange,
        ProposeInput,
        RunOutcome,
        RunTestsInput,
    )


def failure_type(error: BaseException) -> str | None:
    """The error class name an activity failed with, if it failed deliberately."""
    cause = getattr(error, "cause", None)
    if isinstance(cause, ApplicationError):
        return cause.type
    return None


@workflow.defn
class FixWorkflow:
    @workflow.run
    async def run(self, request: FixRequest) -> RunOutcome:
        # Baseline. This single call does three jobs: it exercises the validator,
        # it discovers which tests fail, and it is the FR-017 gate for a
        # repository that is already green.
        baseline = await workflow.execute_activity(
            run_tests,
            RunTestsInput(repo_path=request.repo_path, revision=request.revision, attempt=0),
            start_to_close_timeout=config.RUN_TESTS_TIMEOUT,
            retry_policy=config.retry_policy(),
            heartbeat_timeout=config.RUN_TESTS_HEARTBEAT,
        )
        if baseline.passed:
            return RunOutcome(status="fixed", attempts=[])

        context = await workflow.execute_activity(
            analyze_repo,
            AnalyzeInput(
                repo_path=request.repo_path,
                revision=request.revision,
                baseline=baseline,
            ),
            start_to_close_timeout=config.ANALYZE_TIMEOUT,
            retry_policy=config.retry_policy(),
        )

        attempts: list[Attempt] = []
        for ordinal in range(1, request.max_attempts + 1):
            proposal = await workflow.execute_activity(
                propose_fix,
                ProposeInput(context=context, history=attempts),
                start_to_close_timeout=config.PROPOSE_TIMEOUT,
                heartbeat_timeout=config.PROPOSE_HEARTBEAT,
                retry_policy=config.retry_policy(),
            )

            try:
                applied = await workflow.execute_activity(
                    apply_patch,
                    ApplyInput(
                        repo_path=request.repo_path,
                        revision=request.revision,
                        diff=proposal.diff,
                    ),
                    start_to_close_timeout=config.APPLY_TIMEOUT,
                    retry_policy=config.retry_policy(),
                )
            except ActivityError as error:
                if failure_type(error) != "PatchApplyError":
                    raise
                # A diff that will not apply spends the attempt; it does not end
                # the run. The next proposal sees it in the history.
                attempts.append(Attempt(ordinal=ordinal, change=proposal, applied=False))
                continue

            change = ProposedChange(
                diff=applied.diff,
                rationale=proposal.rationale,
                tree_hash=applied.tree_hash,
            )

            result = await workflow.execute_activity(
                run_tests,
                RunTestsInput(
                    repo_path=request.repo_path,
                    revision=request.revision,
                    diff=change.diff,
                    attempt=ordinal,
                ),
                start_to_close_timeout=config.RUN_TESTS_TIMEOUT,
                retry_policy=config.retry_policy(),
                heartbeat_timeout=config.RUN_TESTS_HEARTBEAT,
            )
            attempts.append(Attempt(ordinal=ordinal, change=change, applied=True, result=result))

            if not result.passed:
                continue

            try:
                branch = await workflow.execute_activity(
                    create_fix_branch,
                    BranchInput(
                        repo_path=request.repo_path,
                        revision=request.revision,
                        change=change,
                    ),
                    start_to_close_timeout=config.BRANCH_TIMEOUT,
                    retry_policy=config.retry_policy(),
                )
            except ActivityError as error:
                if failure_type(error) != "BranchConflictError":
                    raise
                # A fix was found, but the branch name is taken by different
                # content. The patch is preserved; nothing is overwritten.
                return RunOutcome(status="conflicted", attempts=attempts, winning_change=change)

            return RunOutcome(
                status="fixed",
                attempts=attempts,
                winning_change=change,
                branch=branch,
            )

        return RunOutcome(status="exhausted", attempts=attempts)
