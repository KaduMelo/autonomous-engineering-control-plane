---
description: "Task list for 002-ci-trigger-and-pr"
---

# Tasks: CI Trigger and Pull Request

**Input**: Design documents from `/specs/002-ci-trigger-and-pr/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Included and not optional. SC-003, SC-003a, SC-004, SC-006, SC-007, SC-008 and SC-010 each name an automated check, and the constitution's Definition of Done requires proofs of idempotency, the result-vs-exception contract and replay determinism for anything touching the loop.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story the task serves (US1–US4)

## Path Conventions

Single Python project, extending feature 001. New code under `src/control_plane/ingress/`, two new activities, two new adapters. Paths per the Structure Decision in plan.md.

---

## Phase 1: Setup

- [X] T001 Add `starlette`, `uvicorn` and an explicit `httpx2` pin to `pyproject.toml`, and note in a comment that `httpx2` is declared rather than added — it already arrives via `anthropic`, and `httpx` would be a second HTTP stack
- [X] T002 [P] Extend `src/control_plane/config.py` — webhook secret env var, ingress host/port, host API base URL and token env var, clone cache root, and the new timeouts (`CLONE_TIMEOUT`, `CLONE_HEARTBEAT`, `OPEN_PR_TIMEOUT`)
- [X] T003 [P] Add a `bare_remote` pytest fixture in `tests/integration/conftest.py` that creates a bare clone of the seed repository to act as the remote

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: Every user story depends on this phase.

- [X] T004 [P] Add the new domain models in `src/control_plane/domain/models.py` — `BuildNotification`, `IngressOutcome`, `PullRequestRef`, `CloneInput`, `OpenPullRequestInput`; extend `RunOutcome` with `pull_request`
- [X] T005 [P] Add `CloneError` and `HostApiError` in `src/control_plane/domain/errors.py`
- [X] T006 Extend `src/control_plane/adapters/repo.py` with `clone_or_fetch(source, dest)`, `push_ref(repo_path, remote, ref)` and `cache_dir_for(source)` — the deterministic directory derived from the source (depends on T005)
- [X] T007 Implement the `ensure_clone` activity in `src/control_plane/activities/clone.py` — pure function of `(source, revision)`, returns the cache key and never a path, heartbeats while cloning (depends on T006)
- [X] T008 **Refactor feature 001's activities to resolve the repository through `ensure_clone`** in `analyze.py`, `patch.py`, `test_runner.py` and `branch.py`, so a URL source works everywhere a local path did. Feature 001's tests must still pass unchanged (depends on T007) — **done, with a deviation**: `resolve_source()` treats a *working* repository as the repository itself and does not copy it; only a bare or remote source resolves to the clone cache. Cloning a local path would have put the fix branch in a cache directory the operator never looks at, which is exactly what feature 001's demo shows them. All 001 tests pass unchanged.
- [X] T009 [P] Unit test the clone cache in `tests/unit/test_clone_cache.py` — the directory is a deterministic function of the source, a miss clones, a hit fetches, and deleting the directory converges again

**Checkpoint**: a run can be driven from a URL. US1 and US3 can now proceed in parallel.

---

## Phase 3: User Story 1 - A failed build starts a fix without anyone asking (Priority: P1)

**Goal**: A signed build-failure notification starts a run for that commit, with no operator action.

**Independent Test**: post a signed notification for a known commit and observe a run start; post a malformed, unsigned or successful-build one and observe nothing start.

### Tests for User Story 1

- [X] T010 [P] [US1] Unit test signature verification in `tests/unit/test_signature.py` — valid, missing, malformed, wrong-secret and tampered-body cases, and that comparison is constant-time (`hmac.compare_digest`)
- [X] T011 [P] [US1] Unit test payload parsing in `tests/unit/test_notification.py` — required fields, a successful build, and that parsing is never attempted before verification
- [X] T012 [P] [US1] Integration test the ingress in `tests/integration/test_ingress.py` — 202 started, 400 malformed, 401 unsigned, 200 not-a-failure, 503 when the engine is unreachable, and that the response arrives without waiting for the run (SC-003, SC-007)

### Implementation for User Story 1

- [X] T013 [US1] Implement constant-time HMAC verification in `src/control_plane/ingress/signature.py` (depends on T002)
- [X] T014 [US1] Implement the Starlette app in `src/control_plane/ingress/app.py` — read the body as bytes, verify, then parse; start the workflow; return per [contracts/ingress.md](./contracts/ingress.md) (depends on T004, T013)
- [X] T015 [US1] Add the entrypoint in `src/control_plane/ingress/__main__.py` so `python -m control_plane.ingress` serves the app with uvicorn (depends on T014)

**Checkpoint**: a notification starts a run. Nothing is deduplicated yet.

---

## Phase 4: User Story 2 - The same build never starts two runs (Priority: P1)

**Goal**: Ten deliveries of the same notification produce one run, and a commit that already has an answer gets that answer back.

**Independent Test**: send the same notification ten times concurrently; exactly one run exists. Send one for a commit whose run already finished; no new run, and the earlier outcome is reported.

### Tests for User Story 2

- [X] T016 [P] [US2] Integration test concurrent duplicates in `tests/integration/test_ingress_dedup.py` — ten simultaneous posts yield one 202 and nine 200s, and exactly one run (SC-002)
- [X] T017 [P] [US2] Integration test post-completion duplicates in `tests/integration/test_ingress_dedup.py` — a notification for a finished commit starts zero runs and bills the fixer zero times (SC-003a)

### Implementation for User Story 2

- [X] T018 [US2] Start workflows with `id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE` in `src/control_plane/ingress/app.py`, so the engine refuses a second run for a commit in any state (depends on T014)
- [X] T019 [US2] Handle `WorkflowAlreadyStartedError` in `src/control_plane/ingress/app.py` — fetch the existing run's status and answer 200 `deduplicated` with it, never an error (depends on T018)
- [X] T020 [US2] Apply the same reuse policy in `src/control_plane/cli.py`, so a manual run and a webhook run cannot disagree about whether a commit already has an answer (depends on T018) — **done, with an addition**: applying it plainly would have broken `demo.sh`, which re-runs the same revision. The CLI keeps `REJECT_DUPLICATE` as the default and adds `--force` (`TERMINATE_IF_RUNNING`) for an operator who genuinely means to re-run. Safe by default, override visible; both demo scripts pass `--force`.

**Checkpoint**: one commit, one run, whatever the sender does.

---

## Phase 5: User Story 3 - The validated fix becomes exactly one pull request (Priority: P1)

**Goal**: A green run publishes its branch and opens exactly one pull request. Independent of US1 and US2 — this is the back end of the loop.

**Independent Test**: complete a run against the bare remote and confirm one pull request; run the publishing step again and confirm there is still one.

### Tests for User Story 3

- [X] T021 [P] [US3] Unit test the host adapter's response mapping in `tests/unit/test_host_adapter.py` — 201 created, 422-already-exists is a **result**, other 422s and 401/403/404 are non-retryable, 5xx and 429 are retryable, and **no merge call exists anywhere** (SC-006)
- [X] T022 [P] [US3] Unit test the pull request body in `tests/unit/test_pr_body.py` — it names the broken commit, the failing tests, the winning diff and the attempt count (FR-011, SC-009)
- [X] T023 [P] [US3] Integration test `open_pr` in `tests/integration/test_open_pr.py` against the stand-in — one pull request; running twice still one; an existing one is reported with `created=False`; exhausted and conflicted runs open none (SC-004, SC-005)

### Implementation for User Story 3

- [X] T024 [P] [US3] Define `RepositoryHostPort` and implement `GitHubHost` over `httpx2` in `src/control_plane/adapters/host.py`, with a configurable base URL, following [contracts/host_api.md](./contracts/host_api.md) (depends on T005)
- [X] T025 [P] [US3] Implement the runnable stand-in in `scripts/fake_host.py` — a Starlette app over in-memory state implementing both endpoints of the same contract
- [X] T026 [US3] Implement the `open_pr` activity in `src/control_plane/activities/pull_request.py` — push, query, create-if-absent, with the error classification from [contracts/activities.md](./contracts/activities.md) (depends on T006, T024)
- [X] T027 [US3] Render the pull request body in `src/control_plane/activities/pull_request.py` — in the activity, never in the workflow (depends on T026)
- [X] T028 [US3] Extend `FixWorkflow` in `src/control_plane/workflows/fix_workflow.py` to call `open_pr` after `create_fix_branch`, only on `status == "fixed"` (depends on T026)
- [X] T029 [US3] Register `ensure_clone` and `open_pr` in `src/control_plane/worker.py` and extend `NON_RETRYABLE_ERROR_TYPES` in `config.py` (depends on T007, T026)
- [X] T030 [US3] Re-record the replay history with `python scripts/capture_history.py` — the activity sequence changed on purpose, which is exactly when re-recording is meant to happen — and extend `tests/fakes.py` with the two new activities (depends on T028)

**Checkpoint**: green run → exactly one pull request. With US1 and US2, the milestone is complete.

---

## Phase 6: User Story 4 - Knowing what the ingress did (Priority: P2)

**Goal**: Every notification's outcome is legible without reading the run history — which for a rejection contains nothing at all.

**Independent Test**: send accepted, rejected and duplicate notifications and tell the three apart from the ingress's own output.

- [ ] T031 [P] [US4] Unit test outcome recording in `tests/unit/test_ingress_outcomes.py` — every path produces an `IngressOutcome`, and rejections carry an explicit reason (SC-008)
- [ ] T032 [US4] Record and log an `IngressOutcome` on every path in `src/control_plane/ingress/app.py`, with the commit it referred to (depends on T014)

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T033 Write `scripts/demo_webhook.sh` — materialize the seed, create the bare remote, sign a payload, post it, wait for the run, print the pull request; `--duplicate N` sends N concurrently and asserts one run and one pull request
- [ ] T034 [P] Update `.github/workflows/ci.yml` to run the new suite, and assert `SC-010` by running `pytest` with networking unavailable
- [ ] T035 [P] Update `README.md` — the new demo, and the guarantees table gains the dedup and pull request rows
- [ ] T036 [P] Add the webhook secret and host token placeholders to `.env.example`
- [ ] T037 Run [quickstart.md](./quickstart.md) end to end on a clean checkout and correct any drift
- [ ] T038 Confirm feature 001's demos still pass unchanged after the T008 refactor — `./scripts/demo.sh` and `./scripts/demo_kill_resume.sh`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)**: no dependencies
- **Foundational (2)**: needs Setup — blocks everything
- **US1 (3)** and **US3 (5)**: both need Foundational, and **are independent of each other**
- **US2 (4)**: needs US1 — it deduplicates the thing US1 starts
- **US4 (6)**: needs US1
- **Polish (7)**: needs the stories it documents

### User Story Dependencies

Unlike feature 001, two P1 stories here really are independent: US1 is the front end and US3 is the back end, and they touch different files.

```text
Setup → Foundational ─┬─► US1 ──► US2
                      │      └──► US4
                      └─► US3
```

### Within Each User Story

Tests first and failing. Then: adapters → activities → app/workflow → registration.

### Parallel Opportunities

- T002 and T003 in Setup
- T004, T005 and T009 in Foundational (T006–T008 are a chain through the same files)
- T010, T011, T012 together; T016 and T017 together; T021, T022, T023 together
- T024 and T025 are two different files implementing the same contract from both sides
- **US1 and US3 can be built at the same time by different people**
- T034, T035, T036 in Polish

---

## Parallel Example: the two P1 front and back ends

```bash
# Developer A takes the front end:
Task: "Unit test signature verification in tests/unit/test_signature.py"
Task: "Integration test the ingress in tests/integration/test_ingress.py"
Task: "Implement the Starlette app in src/control_plane/ingress/app.py"

# Developer B takes the back end, at the same time:
Task: "Unit test the host adapter mapping in tests/unit/test_host_adapter.py"
Task: "Implement RepositoryHostPort and GitHubHost in src/control_plane/adapters/host.py"
Task: "Implement the runnable stand-in in scripts/fake_host.py"
```

---

## Implementation Strategy

### First increment: Foundational + US3

The back end first. A run started by hand already produces a pull request, which is half the
milestone and needs no HTTP surface at all. It also forces the repository-host contract and the
stand-in into existence early, which is the part most likely to be wrong.

### Second increment: US1 — the trigger

Now a failed build starts the run. At this point `./scripts/demo_webhook.sh` tells the whole story:
notification in, pull request out, nobody watching.

### Third increment: US2 — the property that makes it safe

Deduplication is two settings and their handling, not a subsystem. Small, and without it the
trigger is a liability rather than a feature.

### Then US4 and Polish

Legibility of the ingress, and the demo script.

### A warning about T008

Rewiring feature 001's four activities to resolve the repository through `ensure_clone` is the one
task here that can break something already working and already merged. Feature 001's tests must pass
unchanged afterwards, and T038 re-runs its two demos. If that refactor turns out to be larger than
it looks, it is the task to stop and reconsider at — not the one to push through.

---

## Notes

- `[P]` means different files with no dependency on incomplete work
- Commit after each task or logical group
- Stop at each checkpoint and validate
- The constitution's Definition of Done maps to T030 (replay), T009 and T023 (idempotency), T021
  (result vs. exception), and the acceptance criteria across T012, T016, T017 and T023
