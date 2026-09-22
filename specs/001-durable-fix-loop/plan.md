# Implementation Plan: Durable Self-Fixing Agent Loop

**Branch**: `001-durable-fix-loop` | **Date**: 2026-09-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-durable-fix-loop/spec.md`

## Summary

A red repository is handed to the control plane as a local path plus a revision. The system runs the
suite once in a disposable sandbox to find out what is broken, then iterates propose → apply → validate
until the suite is green or the attempt budget runs out, and finally writes the winning change to a
deterministic branch. Orchestration runs as a Temporal workflow so the run survives the worker dying;
every activity is a pure function of its inputs so retry and resume converge to the same result; the
LLM's code executes only inside a network-less, throwaway container.

The design's load-bearing choice is that **no activity hands filesystem state to another activity**.
`apply_patch` and `run_tests` are both functions of `(revision, patch)` and each rebuilds its own
workspace from a clean checkout. That removes the one thing that would otherwise break resume — a
workspace directory that no longer exists on the worker that picks the run back up.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `temporalio` (durable orchestration), `anthropic` (fix proposer),
`pydantic` (structured LLM output + domain models), Docker CLI (sandbox, driven via subprocess)

**Storage**: None. The Temporal event history is the run's system of record (Principle: observability).
The only durable artifact on disk is the fix branch written into the target repository.

**Testing**: `pytest`, plus `temporalio.worker.Replayer` for the determinism test and
`temporalio.testing.WorkflowEnvironment` (time-skipping) for workflow-level tests

**Target Platform**: Linux host with a Docker runtime and a local Temporal dev server
(`temporal server start-dev`)

**Project Type**: Single Python project — a worker process plus a thin CLI that starts runs

**Performance Goals**: None. Latency is dominated by the LLM and the suite under test; the spec sets no
latency target. What is enforced instead is that every step has a declared ceiling (FR-011).

**Constraints**: Sandbox has no network and no host mounts beyond the workspace; LLM and repository
credentials never enter workflow code or the sandbox; `max_attempts` bounds cost; every activity
declares a start-to-close timeout and `run_tests` heartbeats.

**Scale/Scope**: One repository per run, one run at a time in the demo. ~5 activities, 1 workflow,
1 seed repository. Not multi-tenant, not deployed.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | How this design satisfies it | Status |
|---|---|---|---|
| I. Durable Execution by Default | Run survives worker death; finished activities never re-execute | Temporal workflow; loop state lives in workflow variables replayed from history; no activity depends on another's filesystem side effects, so resume on a fresh worker is total | PASS |
| II. Untrusted Code Never Touches the Host | LLM code runs only in a disposable container with no host reach and no credentials | `docker run --rm --network=none --read-only` with the workspace as the only writable mount, tmpfs for scratch, non-root user, memory and pid caps, and an env allowlist that carries no secrets | PASS |
| III. Idempotent Side Effects | Every writer is safe to re-run | `apply_patch` and `run_tests` are functions of `(revision, patch)` that rebuild from a clean checkout; `create_fix_branch` checks-if-exists and refuses to overwrite divergent content | PASS |
| IV. Deterministic Workflows, Impure Activities | No I/O in workflow code; replay test enforced | All I/O in activities; `workflow.unsafe.imports_passed_through()` around domain/activity imports; a recorded history replayed by `Replayer` in CI | PASS |
| V. Result vs. Exception (NON-NEGOTIABLE) | Red suite is a result; broken runner is an error | Exit-code contract: pytest `0` → passed, `1` → not-passed (a `TestResult`), anything else (including `2`–`5`, `125`–`127`, `137`) → `TestRunnerError` raised. Covered by tests on the runner itself | PASS |
| VI. Bounded Cost and Bounded Time | Explicit ceilings everywhere | `max_attempts` on the loop; `start_to_close_timeout` on all five activities; `heartbeat_timeout` on `run_tests` with a heartbeat task; the Anthropic client's own retries are disabled so ceilings are not multiplied | PASS |
| VII. Mechanism Density over Feature Count | Every dependency earns a guarantee the stack lacks | Four dependencies, each tied to one guarantee (durability, isolation, fix generation, schema-valid output). No web framework, no database, no tracing stack in this feature | PASS |

**Locked negative scope respected**: no webhook ingress, no approval gate, no pull request, no
multi-tenancy, no UI, no hosted Temporal. All deferred to later features.

**Post-design re-check (after Phase 1)**: PASS — no new violations. The two design decisions that
add work rather than remove it (rebuilding the workspace twice per attempt; disabling the SDK's own
retries) both exist to satisfy Principles I and VI and are recorded in Complexity Tracking below.

## Project Structure

### Documentation (this feature)

```text
specs/001-durable-fix-loop/
├── plan.md              # This file
├── research.md          # Phase 0 output — decisions with alternatives rejected
├── data-model.md        # Phase 1 output — entities and state transitions
├── quickstart.md        # Phase 1 output — run it end to end, including the kill-resume demo
├── contracts/
│   ├── activities.md    # The five activity contracts: signature, errors, timeouts, idempotency
│   └── fix_proposal.schema.json  # Structured-output schema the fixer must return
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/control_plane/
├── domain/
│   ├── models.py             # FixRequest, RepositoryContext, Attempt, ProposedChange,
│   │                         # TestResult, RunOutcome, FixBranch — frozen, serializable
│   └── errors.py             # TestRunnerError, PatchApplyError, BranchConflictError
├── workflows/
│   └── fix_workflow.py       # FixWorkflow — the only deterministic module; zero I/O
├── activities/
│   ├── analyze.py            # analyze_repo
│   ├── propose.py            # propose_fix
│   ├── patch.py              # apply_patch
│   ├── test_runner.py        # run_tests
│   └── branch.py             # create_fix_branch
├── adapters/
│   ├── fixer.py              # FixerPort protocol + AnthropicFixer implementation
│   ├── sandbox.py            # Docker invocation, isolation flags, exit-code contract
│   └── repo.py               # git plumbing: clean checkout, apply, commit, branch refs
├── config.py                 # timeouts, max_attempts, image tag, model id — one place
├── worker.py                 # registers workflow + activities, runs the worker
└── cli.py                    # `fixctl run --repo <path> --sha <sha>`

sandbox/
└── Dockerfile                # prebuilt image: python + the seed suite's dependencies, no network at run time

seed/
└── broken-calculator/        # versioned seed repository with a deterministic failing test

scripts/
├── demo.sh                   # reproducible end-to-end run
└── demo_kill_resume.sh       # starts a run, kills the worker mid-flight, restarts it

tests/
├── unit/                     # exit-code contract, patch normalization, branch conflict logic
├── integration/              # real Docker sandbox, real git, fake fixer
└── replay/
    ├── test_determinism.py   # Replayer against the recorded history
    └── histories/            # checked-in event history JSON
```

**Structure Decision**: Single Python package under `src/control_plane/`, split by Temporal's own
seam — `workflows/` is deterministic and import-restricted, `activities/` is where I/O lives, and
`adapters/` holds the three external boundaries (LLM, Docker, git) behind narrow interfaces. The
split is not decorative: it is what makes Principle IV mechanically checkable, because any import of
`adapters/` from `workflows/` is a determinism bug a reviewer can spot without running anything.
`sandbox/`, `seed/` and `scripts/` sit outside the package because they are artifacts the demo needs,
not importable code.

## Complexity Tracking

> Recorded for transparency. Neither is a constitution violation — both add work in order to satisfy a
> principle — but both cost something and a reviewer will ask why.

| Decision | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `apply_patch` and `run_tests` each rebuild the workspace from a clean checkout, so the checkout runs twice per attempt | Makes both activities pure functions of `(revision, patch)`. No filesystem state crosses an activity boundary, so a resume on a different worker — or after the workspace was garbage-collected — behaves identically to a first run (Principles I, III) | Passing a workspace path from `apply_patch` to `run_tests` is one checkout cheaper, but it makes `run_tests` depend on state that does not survive worker death. On resume, the completed `apply_patch` is not re-executed, so its path would point at a directory that no longer exists and the run would wedge in a retry loop |
| The Anthropic SDK's built-in retries are disabled (`max_retries=0`); Temporal's retry policy is the only retry layer | Two retry layers multiply: 3 Temporal attempts × 3 SDK attempts is 9 billed calls behind one activity, and the history would show 3. That breaks both the cost ceiling (Principle VI) and the "zero re-calls on resume" measurement in SC-003 | Leaving the SDK default (2 retries) is less code, but the run's recorded history would stop being an accurate account of what was actually spent — and that history is the evidence the whole POC is built to show |
