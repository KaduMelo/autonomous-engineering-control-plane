# Activity Contracts

**Feature**: `001-durable-fix-loop` | **Date**: 2026-09-22

Five activities. For each: what it promises, what it raises, its ceilings, and why re-running it is
safe. The workflow calls nothing else and performs no I/O of its own.

Shared retry policy unless stated otherwise: `initial_interval=1s`, `backoff_coefficient=2.0`,
`maximum_interval=60s`, `maximum_attempts=3`.

---

## `run_tests(revision: str, patch: str | None) -> TestResult`

**Promises**: Executes the suite at `revision` with `patch` applied (or pristine when `patch is None`)
inside a disposable container with no network, and returns a verdict.

**Raises**: `TestRunnerError` for every exit code that is not `0` or `1` — see the exit-code table in
[research.md](../research.md) D4. A failing suite is **never** an exception.

**Ceilings**: `start_to_close_timeout=600s`; `heartbeat_timeout=30s` with a heartbeat every 5s while
the container runs; the container itself is killed at 300s by `asyncio.wait_for`, so the inner limit
always fires before the outer one and the failure is attributable.

**Idempotent because**: it is a pure function of `(revision, patch)`. It builds its own workspace from
a clean checkout and destroys it. Re-running produces the same verdict and leaves nothing behind.

**Called**: once as the baseline (`patch=None`), then once per applied attempt.

---

## `analyze_repo(repo_path: str, revision: str, baseline: TestResult) -> RepositoryContext`

**Promises**: Gathers the failing test files named in `baseline.failing_tests` plus the modules they
import, at `revision`, bounded in size.

**Raises**: `ApplicationError(non_retryable=True)` when `revision` does not exist in the repository —
retrying cannot fix a missing commit. Ordinary I/O errors propagate and are retried.

**Ceilings**: `start_to_close_timeout=60s`.

**Idempotent because**: read-only. No side effects at all.

---

## `propose_fix(context: RepositoryContext, history: list[Attempt]) -> ProposedChange`

**Promises**: Returns a schema-valid unified diff against `revision`, with a rationale. `history`
carries the previous attempts' failure output, which is what makes iteration converge (FR-007).

**Raises**:
- Retryable (propagate as-is): `RateLimitError`, `InternalServerError`, `APIConnectionError`,
  `APITimeoutError`.
- Non-retryable (wrapped in `ApplicationError(non_retryable=True)`, listed in
  `non_retryable_error_types`): `BadRequestError`, `AuthenticationError`, `PermissionDeniedError`,
  `NotFoundError`.

**Ceilings**: `start_to_close_timeout=300s`; the Anthropic client's own `timeout=240.0` fires first.
`max_retries=0` on the client — Temporal is the only retry layer (research D5).

**Idempotent because**: it has no side effect beyond the billed call. It is *not* deterministic, and
it does not need to be: Temporal records the result in the history, so a resumed run replays the
recorded proposal rather than asking again. That is precisely what SC-003 measures.

**Credential boundary**: the API key is read here, inside the activity. It never enters workflow code
and never enters the sandbox (FR-016).

---

## `apply_patch(repo_path: str, revision: str, diff: str) -> ProposedChange`

**Promises**: Verifies the diff applies cleanly to a clean checkout of `revision` and returns the
change with `tree_hash` populated.

**Raises**: `PatchApplyError` when the diff is malformed or does not apply. The workflow catches this,
marks the attempt `applied=False`, and continues to the next attempt — it does not abort the run.

**Ceilings**: `start_to_close_timeout=60s`.

**Idempotent because**: it always starts from a clean checkout of `revision`, so the diff can never
stack on a previous application (FR-008). Running it twice yields an identical `tree_hash`. It writes
nothing to the operator's repository — the checkout is a throwaway.

---

## `create_fix_branch(repo_path: str, revision: str, change: ProposedChange) -> FixBranch`

**Promises**: Ensures `fix/<revision>` exists in the target repository, pointing at a commit that
applies `change.diff` on top of `revision`.

**Behavior**:

| Existing state of `fix/<revision>` | Action | Result |
|---|---|---|
| Absent | Create the ref | `FixBranch(created=True)` |
| Present, commit's tree matches `change.tree_hash` | Nothing | `FixBranch(created=False)` — idempotent no-op |
| Present, tree differs | Nothing | `BranchConflictError` → run ends `conflicted`, nothing overwritten |

**Raises**: `BranchConflictError` (non-retryable — retrying cannot resolve it); ordinary git failures
propagate and are retried.

**Ceilings**: `start_to_close_timeout=60s`.

**Idempotent because**: check-if-exists before write, and the branch name is a deterministic function
of `revision` (FR-019, FR-020). A retry or a resume after the ref was already written finds it,
matches the tree, and returns the no-op.

**Must not**: change `HEAD`, the index, or the working tree. The ref is written directly. This is what
SC-011 verifies.

---

## What the workflow itself does

Only orchestration: call the activities above in order, hold `attempt` and `history` in local
variables, compare `attempt` against `max_attempts`, and assemble the `RunOutcome`. No I/O, no clock
reads except `workflow.now()`, no randomness, no imports from `adapters/`. Everything it imports goes
through `workflow.unsafe.imports_passed_through()` (Principle IV).
