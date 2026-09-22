---
description: "Task list for 001-durable-fix-loop"
---

# Tasks: Durable Self-Fixing Agent Loop

**Input**: Design documents from `/specs/001-durable-fix-loop/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Test tasks are included and are **not optional here**. The spec asks for them by name — SC-004, SC-005, SC-006, SC-008, SC-010 and SC-011 each say "verified by an automated test" — and the constitution's Definition of Done requires five specific proofs before any feature touching the agent loop is done.

**Organization**: Grouped by user story so each can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story the task serves (US1–US4)

## Path Conventions

Single Python project. Source under `src/control_plane/`, tests under `tests/`, with `sandbox/`, `seed/` and `scripts/` at the repository root — per the Structure Decision in plan.md.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: The skeleton and the two fixtures everything else runs against.

- [X] T001 Create the package and test tree per plan.md in `src/control_plane/{domain,workflows,activities,adapters}/` and `tests/{unit,integration,replay}/`, each with `__init__.py`
- [X] T002 Initialize the project in `pyproject.toml` — Python 3.12, dependencies `temporalio`, `anthropic`, `pydantic`; dev dependencies `pytest`, `pytest-asyncio`, `ruff`, `mypy`
- [X] T003 [P] Configure `ruff` and `mypy` in `pyproject.toml`, with `src/control_plane/workflows/` set to disallow imports from `adapters` so Principle IV is machine-checked
- [X] T004 [P] Build the seed repository in `seed/broken-calculator/` — a small Python package with a deterministic failing test, committed so its revision sha is stable
- [X] T005 [P] Write `sandbox/Dockerfile` — `python:3.12-slim`, pytest and the seed suite's dependencies installed at build time, a non-root user, and no entrypoint that needs network

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Types and the git plumbing every activity needs.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T006 [P] Define the domain models in `src/control_plane/domain/models.py` — `FixRequest`, `SourceFile`, `RepositoryContext`, `ProposedChange`, `TestResult`, `Attempt`, `FixBranch`, `RunOutcome`, per data-model.md. Frozen, serializable, no live handles
- [X] T007 [P] Define the error types in `src/control_plane/domain/errors.py` — `TestRunnerError`, `PatchApplyError`, `BranchConflictError`
- [X] T008 [P] Centralize configuration in `src/control_plane/config.py` — timeouts, `max_attempts`, sandbox image tag, model id, task queue, workspace root
- [X] T009 Implement the git adapter in `src/control_plane/adapters/repo.py` — `clean_checkout(repo_path, revision)` into a throwaway directory, `apply_diff`, `tree_hash`, and ref read/write helpers that never touch `HEAD`, the index or the worktree (depends on T007, T008)
- [X] T010 Create the worker entrypoint in `src/control_plane/worker.py` registering the workflow and activities against the configured task queue (depends on T008)

**Checkpoint**: Types and git plumbing exist. User story work can begin.

---

## Phase 3: User Story 1 - Objective validation of a candidate fix (Priority: P1) 🎯 Foundation

**Goal**: Answer "does this code pass its tests?" honestly, in a throwaway container with no network, without the tested code ever touching the host.

**Independent Test**: Point the validator at the red seed repository and at a known-green one; it returns not-passed with output for the first and passed for the second. Then break the runtime and confirm it raises an infrastructure error instead of reporting "tests failed".

### Tests for User Story 1

> Write these first and watch them fail.

- [X] T011 [P] [US1] Unit test the exit-code contract in `tests/unit/test_exit_code_contract.py` — `0` → passed, `1` → not-passed, and `2`, `3`, `4`, `5`, `125`, `126`, `127`, `137` each raise `TestRunnerError` (SC-005)
- [X] T012 [P] [US1] Integration test isolation in `tests/integration/test_sandbox_isolation.py` — code inside the container cannot reach the network and cannot write outside the workspace mount (SC-004)

### Implementation for User Story 1

- [X] T013 [US1] Implement the sandbox invocation in `src/control_plane/adapters/sandbox.py` — `docker run --rm --network=none --read-only`, tmpfs `/tmp`, workspace bind mount, non-root user, memory/pids/cpu caps, no secret env; launched with `asyncio.create_subprocess_exec` under `asyncio.wait_for`, force-removing the container on timeout
- [X] T014 [US1] Implement the exit-code mapper in `src/control_plane/adapters/sandbox.py`, making T011 pass (depends on T013)
- [X] T015 [US1] Implement the `run_tests` activity in `src/control_plane/activities/test_runner.py` — build the workspace from a clean checkout of `(revision, patch)`, run the sandbox, emit `activity.heartbeat()` every 5s while the container runs (depends on T009, T014)
- [X] T016 [US1] Register `run_tests` in `src/control_plane/worker.py` with `start_to_close_timeout=600s` and `heartbeat_timeout=30s`, so the inner 300s container limit always fires first (depends on T010, T015) — **note**: registration is in `worker.py`, but Temporal declares activity timeouts at the *call site*, so the two values live in `config.py` and are applied by the workflow's `execute_activity` (T028)
- [X] T017 [US1] Integration test the activity end to end in `tests/integration/test_run_tests.py` — red seed returns not-passed, green returns passed, a hanging suite raises rather than blocking

**Checkpoint**: The validator works standalone. Note it is *not* yet a demo of the feature's value — it is the foundation US2 and US3 both stand on.

---

## Phase 4: User Story 2 - Iterating to a green fix within a bounded budget (Priority: P1)

**Goal**: A red repository becomes green with no human in the loop, inside an attempt budget, ending on a deterministic branch.

**Independent Test**: Run the loop against the red seed and watch it reach green within the budget; run it against an unfixable failure and watch it stop exactly at the budget, reporting exhaustion.

### Tests for User Story 2

- [ ] T018 [P] [US2] Unit test apply idempotency in `tests/unit/test_apply_patch_idempotent.py` — applying the same diff twice yields an identical tree hash and never stacks (SC-006)
- [ ] T019 [P] [US2] Integration test branch behavior in `tests/integration/test_branch_idempotency.py` — absent → created; present with the same patch → no-op success; present and divergent → `BranchConflictError`, nothing overwritten (SC-010)
- [ ] T020 [P] [US2] Integration test in `tests/integration/test_operator_repo_untouched.py` — compare `HEAD`, index and worktree before and after a successful run; they must be identical (SC-011)
- [ ] T021 [P] [US2] Workflow loop test in `tests/integration/test_fix_workflow_loop.py` using `WorkflowEnvironment.start_time_skipping()` and fake activities — stops at first green, never exceeds `max_attempts`, a patch that fails to apply spends the attempt and continues, and an already-green repository ends with zero attempts (FR-017)

### Implementation for User Story 2

- [ ] T022 [P] [US2] Define the `FixerPort` protocol in `src/control_plane/adapters/fixer.py` so the workflow depends on an interface, not a vendor
- [ ] T023 [US2] Implement `AnthropicFixer` in `src/control_plane/adapters/fixer.py` — `claude-opus-5`, `thinking={"type": "adaptive"}`, `output_config={"effort": "high"}`, `max_tokens=16000`, `client.messages.parse(output_format=FixProposal)` against [contracts/fix_proposal.schema.json](./contracts/fix_proposal.schema.json), and **`AsyncAnthropic(max_retries=0, timeout=240.0)`** so Temporal is the only retry layer (depends on T022)
- [ ] T024 [P] [US2] Implement the `analyze_repo` activity in `src/control_plane/activities/analyze.py` — gather the failing test files named in the baseline result plus the modules they import, bounded in size (depends on T009)
- [ ] T025 [P] [US2] Implement the `propose_fix` activity in `src/control_plane/activities/propose.py` — classify errors: `RateLimitError`/`InternalServerError`/`APIConnectionError`/`APITimeoutError` propagate for retry; `BadRequestError`/`AuthenticationError`/`PermissionDeniedError`/`NotFoundError` wrap in a non-retryable `ApplicationError`. The API key is read here and nowhere else (depends on T023)
- [ ] T026 [P] [US2] Implement the `apply_patch` activity in `src/control_plane/activities/patch.py` — clean checkout, apply, return the change with `tree_hash`; raise `PatchApplyError` when the diff does not apply (depends on T009)
- [ ] T027 [P] [US2] Implement the `create_fix_branch` activity in `src/control_plane/activities/branch.py` — resolve `fix/<revision>`, check-if-exists, write the ref directly without touching `HEAD` (depends on T009)
- [ ] T028 [US2] Implement `FixWorkflow` in `src/control_plane/workflows/fix_workflow.py` — baseline `run_tests(revision, None)` first, then the propose→apply→validate loop, then `create_fix_branch`; assemble `RunOutcome` with status `fixed`, `exhausted` or `conflicted` (depends on T024–T027)
- [ ] T029 [US2] Register the remaining activities in `src/control_plane/worker.py` with their start-to-close timeouts from `config.py` (depends on T028)
- [ ] T030 [US2] Implement the CLI in `src/control_plane/cli.py` — `fixctl run --repo <path> --sha <sha>`, starting the workflow with `workflow_id=f"fix-{revision}"` so duplicate starts are rejected by the engine (depends on T029)

**Checkpoint**: Red seed → green PR-ready branch, end to end. This is the first increment with demonstrable value.

---

## Phase 5: User Story 3 - Surviving the death of the executing process (Priority: P1)

**Goal**: A run killed on attempt 3 resumes on attempt 3, with zero re-billed proposals for attempts that had already finished.

**Independent Test**: Start a run, wait until it is provably past attempt 2, kill the worker, start a fresh one, and read from the event history that execution resumed where it stopped and that `propose_fix` appears exactly once per attempt.

### Tests for User Story 3

- [ ] T031 [P] [US3] Determinism test in `tests/replay/test_determinism.py` — `temporalio.worker.Replayer` against a checked-in history, failing loudly on any non-deterministic change to workflow code (SC-008)

### Implementation for User Story 3

- [ ] T032 [US3] Enforce determinism in `src/control_plane/workflows/fix_workflow.py` — wrap domain and activity-stub imports in `workflow.unsafe.imports_passed_through()`, remove every clock read, random source and I/O call, using `workflow.now()` where time is genuinely needed (depends on T028)
- [ ] T033 [US3] Configure retry policies in `src/control_plane/config.py` and `worker.py` — shared backoff (1s initial, ×2, 60s cap, 3 attempts) and `non_retryable_error_types` listing `BranchConflictError`, `PatchApplyError` and the non-retryable LLM failures (depends on T029)
- [ ] T034 [US3] Capture a real run's event history into `tests/replay/histories/` and wire it into T031 (depends on T030, T032)
- [ ] T035 [US3] Write `scripts/demo_kill_resume.sh` — start a run, wait until the history shows attempt 3 in flight, kill the worker, start a replacement, and print where it resumed (depends on T030)
- [ ] T036 [US3] Add the SC-003 verification recipe to [quickstart.md](./quickstart.md) — how to count `propose_fix` entries in the history and why `max_retries=0` is what makes that count trustworthy

**Checkpoint**: The headline guarantee is demonstrable and the determinism regression is caught automatically.

---

## Phase 6: User Story 4 - Inspecting what a run did (Priority: P2)

**Goal**: Reconstruct a run's attempt-by-attempt narrative from the recorded history alone.

**Independent Test**: After any finished run, identify each attempt's proposal, application outcome and verdict in order, plus the terminal outcome and its reason, without additional instrumentation.

- [ ] T037 [P] [US4] Ensure `RunOutcome` carries the full `Attempt` list including each proposal's diff and rationale, so a green verdict can be audited against what actually changed (FR-013, SC-009) in `src/control_plane/workflows/fix_workflow.py`
- [ ] T038 [US4] Include attempt ordinal and elapsed time in the `run_tests` heartbeat details in `src/control_plane/activities/test_runner.py`, so an in-flight run is legible in the Temporal UI (depends on T015)
- [ ] T039 [US4] Integration test in `tests/integration/test_run_record.py` — a finished run's outcome reconstructs the full narrative, and an exhausted run states its terminal reason explicitly

**Checkpoint**: All four user stories independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T040 [P] Write `scripts/demo.sh` — reset the seed repository to its red revision, delete any leftover `fix/<sha>` branch, start the run
- [ ] T041 [P] Rewrite `README.md` — what the control plane is, the two demos, and links to spec, plan and constitution
- [ ] T042 [P] Add `.github/workflows/ci.yml` — ruff, mypy, and pytest for the unit and replay suites; the integration suite gated on a Docker runtime being available
- [ ] T043 [P] Add `.env.example` with an `ANTHROPIC_API_KEY` placeholder, matching the `.gitignore` exclusion
- [ ] T044 Run [quickstart.md](./quickstart.md) end to end on a clean checkout and correct any drift between what it says and what the code does

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: needs Setup — blocks every user story
- **US1 (Phase 3)**: needs Foundational
- **US2 (Phase 4)**: needs Foundational, and needs US1's `run_tests` for the baseline call and the validation step
- **US3 (Phase 5)**: needs US2's workflow to exist before it can be made replay-safe
- **US4 (Phase 6)**: needs US2's workflow; independent of US3
- **Polish (Phase 7)**: needs the stories it documents

### User Story Dependencies — read this before planning parallel work

The template's default assumption is that stories are independent. **Here they are not, and pretending otherwise would break the build.** US2 consumes US1's validator directly; US3 hardens the workflow US2 creates. The honest order is US1 → US2 → US3, with US4 branching off US2 in parallel with US3.

```text
Setup → Foundational → US1 ──→ US2 ──┬──→ US3
                                      └──→ US4
```

### Within Each User Story

Tests are written first and must fail. Then: adapters → activities → workflow → registration → CLI.

### Parallel Opportunities

- T003, T004, T005 in Setup
- T006, T007, T008 in Foundational (T009 and T010 depend on them)
- T011 and T012 together; T018–T021 together
- T024, T025, T026, T027 are four separate activity files with no mutual dependencies
- T040–T043 in Polish

---

## Parallel Example: User Story 2

```bash
# All four US2 tests first — different files, no shared state:
Task: "Unit test apply idempotency in tests/unit/test_apply_patch_idempotent.py"
Task: "Integration test branch behavior in tests/integration/test_branch_idempotency.py"
Task: "Integration test operator repo untouched in tests/integration/test_operator_repo_untouched.py"
Task: "Workflow loop test in tests/integration/test_fix_workflow_loop.py"

# Then the four activities, once the adapters they need exist:
Task: "analyze_repo activity in src/control_plane/activities/analyze.py"
Task: "propose_fix activity in src/control_plane/activities/propose.py"
Task: "apply_patch activity in src/control_plane/activities/patch.py"
Task: "create_fix_branch activity in src/control_plane/activities/branch.py"
```

---

## Implementation Strategy

### First increment: US1

Complete Setup + Foundational + US1. You get a trustworthy validator — the thing every later step consumes as its feedback signal, with the result-vs-exception contract already covered by tests.

**Be honest about what this is**: US1 alone is not a demo of the feature. It answers "do these tests pass?" and nothing more. It is first because the spec says the loop is worthless without it, not because it ships value on its own.

### Second increment: US2 — the first demo

Red seed repository → green, ending on `fix/<sha>`. This is the first thing worth showing anyone.

### Third increment: US3 — the demo that matters

Kill the worker mid-run and watch it resume without re-paying. This is the guarantee the whole POC exists to demonstrate, and the reason Temporal is in the stack at all. If time runs short, this beats US4 and every polish task combined.

### Then US4 and Polish

Observability and reproducibility. Valuable, but neither is what a technical reviewer is evaluating.

---

## Notes

- `[P]` means different files with no dependency on incomplete work
- Commit after each task or logical group
- Stop at each checkpoint and validate before moving on
- The five Definition-of-Done proofs from the constitution map to T031 (replay), T035 (kill-resume), T018 (idempotent apply), T011 (result vs. exception) and the acceptance criteria covered across T012, T017, T019, T020, T021
