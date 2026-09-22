# Phase 1 Data Model: Durable Self-Fixing Agent Loop

**Feature**: `001-durable-fix-loop` | **Date**: 2026-09-22

Every type below crosses a Temporal activity boundary, so all of them must serialize cleanly and none
may carry a handle to anything live — no file objects, no clients, no paths that outlive an activity.
That constraint is what makes resume work, and it is why `WorkspaceRef` does **not** appear here.

## Entities

### FixRequest — the run's input

| Field | Type | Notes |
|---|---|---|
| `repo_path` | `str` | Absolute local path to the target repository (clarified: local path, not a remote) |
| `revision` | `str` | Full 40-character commit sha. Determines `workflow_id = fix-<revision>` (FR-001) |
| `max_attempts` | `int` | Attempt budget, default 3. Bounds cost (FR-006, Principle VI) |

### RepositoryContext — what the fixer gets to see

| Field | Type | Notes |
|---|---|---|
| `failing_tests` | `list[str]` | pytest node ids, **discovered** by the baseline run, never supplied by the operator (FR-002) |
| `failure_output` | `str` | Captured output from the baseline validation |
| `sources` | `list[SourceFile]` | The test files and the modules they import |

### SourceFile

| Field | Type | Notes |
|---|---|---|
| `path` | `str` | Repository-relative |
| `content` | `str` | Content at `revision` |

### ProposedChange — one candidate fix

| Field | Type | Notes |
|---|---|---|
| `diff` | `str` | Unified diff against `revision` (FR-008, clarified) |
| `rationale` | `str` | The fixer's explanation; carried into the run record for human review (FR-018) |
| `tree_hash` | `str \| None` | Populated by `apply_patch` once the diff is known to apply. `None` before then |

May touch any path, including tests (FR-018).

### TestResult — a verdict, never an error

| Field | Type | Notes |
|---|---|---|
| `passed` | `bool` | `True` only on exit code `0` |
| `output` | `str` | Captured stdout+stderr, truncated to a bounded size |
| `failing_tests` | `list[str]` | Node ids parsed from the report; empty when `passed` |
| `duration_s` | `float` | Wall-clock inside the sandbox |

A broken runner never produces a `TestResult` — it raises `TestRunnerError` (Principle V).

### Attempt — one pass of the loop

| Field | Type | Notes |
|---|---|---|
| `ordinal` | `int` | 1-based |
| `change` | `ProposedChange` | What was tried |
| `applied` | `bool` | `False` when the diff did not apply — the attempt is spent, the run continues (FR-008 edge case) |
| `result` | `TestResult \| None` | `None` when `applied` is `False` |

### FixBranch — the deliverable

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | `fix/<revision>` (deterministic, FR-019) |
| `commit` | `str` | Sha of the commit applying the winning patch on top of `revision` |
| `created` | `bool` | `False` when the branch already existed carrying the same patch — the idempotent no-op (FR-020) |

### RunOutcome — the terminal state

| Field | Type | Notes |
|---|---|---|
| `status` | `"fixed" \| "exhausted" \| "conflicted"` | |
| `attempts` | `list[Attempt]` | Every attempt, in order (FR-013) |
| `winning_change` | `ProposedChange \| None` | Set on `fixed` and on `conflicted` — preserved either way (FR-018, SC-009) |
| `branch` | `FixBranch \| None` | Set on `fixed` only |

## State transitions

```text
                      run_tests(revision, patch=None)          <- baseline; discovers what is broken
                                  |
                   passed? -------+------- not passed
                      |                        |
              fixed (0 attempts)          analyze_repo
                 [FR-017]                      |
                                               v
                             +-----> propose_fix (attempt N)
                             |                 |
                             |            apply_patch
                             |            /          \
                             |     did not apply    applied
                             |      (attempt spent)     |
                             |            |         run_tests(revision, patch)
                             |            |          /              \
                             |            |     not passed        passed
                             |            |         |                |
                             +------------+---------+        create_fix_branch
                             |  N < max_attempts             /       |        \
                             |                          created   no-op    divergent
                        N == max_attempts                   \       /          |
                             |                               fixed         conflicted
                        exhausted
```

Three terminal states, all recorded with the full attempt list. `exhausted` and `conflicted` are
outcomes, not failures — the workflow completes rather than erroring, so the history stays readable.

## Serialization rules

1. **Frozen and plain.** Pydantic models with immutable semantics; no behavior beyond validation.
2. **No live handles.** No paths that outlive an activity, no open files, no clients. This is the rule
   that makes D2 (pure activities) enforceable by inspection.
3. **Bounded.** `TestResult.output` and `SourceFile.content` are truncated before crossing the
   boundary — Temporal payloads have limits, and an unbounded suite log would blow the history.
4. **Additive evolution.** New fields are optional with defaults, so a history recorded before the
   change still replays (Principle IV).
