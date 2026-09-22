# Phase 0 Research: Durable Self-Fixing Agent Loop

**Feature**: `001-durable-fix-loop` | **Date**: 2026-09-22

Every decision below is recorded as Guarantee → Mechanism → Evidence → Trade-off, per Principle VII.
No `NEEDS CLARIFICATION` remained after the clarification session, so this phase resolves design
questions rather than requirement gaps.

---

## D1 — Baseline validation replaces a separate "discover the failing test" step

**Guarantee**: The system knows which tests fail without being told (FR-002), and a repository that is
already green terminates in zero attempts (FR-017).

**Mechanism**: The workflow's first act is `run_tests(revision, patch=None)` against the pristine
revision. Its `TestResult` is both the discovery mechanism and the FR-017 gate: `passed=True` ends the
run immediately as fixed-with-zero-attempts; `passed=False` carries the failing node ids and output
into `analyze_repo`, which only has to gather the associated source.

**Evidence**: One activity serves three requirements (FR-002, FR-017, and the US1 validator itself),
and the very first thing any run exercises is the component US1 declares to be the foundation.

**Trade-off**: One extra sandbox run per run, before any fix work. Negligible against LLM latency, and
it buys the fact that discovery and validation can never disagree — they are literally the same code
path. The alternative, parsing a CI report or having the operator name the test, was rejected in
clarification: it introduces a second source of truth about what is broken.

---

## D2 — Activities are pure functions of `(revision, patch)`; no workspace crosses an activity boundary

**Guarantee**: Resume after worker death is total, and retry is idempotent (Principles I and III).

**Mechanism**: `apply_patch(revision, patch)` creates its own throwaway checkout, verifies the patch
applies, and returns the *normalized patch plus the resulting tree hash* — never a path.
`run_tests(revision, patch)` independently creates its own checkout, applies the same patch, and runs
the suite. Neither reads state the other wrote.

**Evidence**: The failure this prevents is concrete. With a path handed between activities: a run dies
after `apply_patch` completes; on resume Temporal does not re-execute it (that is the whole point of
Principle I), so `run_tests` receives a path to a directory that the dead worker's cleanup — or a
different machine entirely — no longer has. The activity fails, Temporal retries it, and it fails
identically forever. The run wedges. With pure functions there is nothing to lose.

**Trade-off**: Two checkouts per attempt instead of one. For the seed repository this is milliseconds.
Recorded in the plan's Complexity Tracking because the cost is real for a large repository and a
reviewer should see that it was chosen, not overlooked.

---

## D3 — Sandbox isolation: `docker run` via subprocess, not the Docker SDK

**Guarantee**: LLM-generated code cannot reach the host, the network, or any credential (Principle II).

**Mechanism**: One `docker run` invocation per validation, with the isolation flags explicit:

| Flag | What it closes |
|---|---|
| `--network=none` | Exfiltration and dependency fetching at run time (clarified decision) |
| `--read-only` | Writes anywhere outside the declared mounts |
| `--tmpfs /tmp:rw,noexec,nosuid,size=64m` | Scratch space the suite needs, non-executable |
| `--mount type=bind,src=<workspace>,dst=/work` | The only writable project path; a throwaway copy, never the operator's repository |
| `--user <non-root uid>` | Root inside the container |
| `--memory`, `--pids-limit`, `--cpus` | Fork bombs and memory exhaustion taking the host with them |
| `--rm` | Leftover containers |
| No `--env` carrying secrets | Credentials reaching generated code |

The process is launched with `asyncio.create_subprocess_exec` and awaited under `asyncio.wait_for`;
on timeout the container is force-removed and the activity raises.

**Evidence**: The flags are the security boundary, so they are readable in one place as literal
arguments. A reviewer can audit isolation by reading a single list.

**Trade-off**: The `docker` Python SDK is more ergonomic and more mockable. It was rejected because it
buries the boundary in keyword arguments spread across a call, and because subprocess keeps the
dependency surface smaller (Principle VII). Testability is recovered by putting the invocation behind
`adapters/sandbox.py`, which integration tests exercise for real and unit tests bypass.

---

## D4 — The result-vs-exception contract is an exit-code table

**Guarantee**: A failing test is never mistaken for a broken runner, in either direction
(Principle V, NON-NEGOTIABLE).

**Mechanism**: `run_tests` maps the container's exit code:

| Exit code | Meaning | Contract |
|---|---|---|
| `0` | pytest: all tests passed | `TestResult(passed=True)` |
| `1` | pytest: tests failed | `TestResult(passed=False, output=...)` — **a result** |
| `2` | pytest: interrupted | `TestRunnerError` |
| `3` | pytest: internal error | `TestRunnerError` |
| `4` | pytest: usage error | `TestRunnerError` |
| `5` | pytest: no tests collected | `TestRunnerError` |
| `125`–`127` | docker could not run the container / command not found | `TestRunnerError` |
| `137` | SIGKILL — OOM or forced timeout kill | `TestRunnerError` |
| anything else | unknown | `TestRunnerError` |

`TestRunnerError` propagates so Temporal retries it. `TestResult` returns normally and feeds the loop.

**Evidence**: Only exit `0` and `1` are results; everything else is an error, including the cases most
likely to be mistaken for a red suite — `5` (no tests collected, which a deleted test file can cause)
and `137` (killed, which a hanging suite causes). Both are covered by unit tests on the mapper.

**Trade-off**: The table is pytest-specific, so a non-pytest repository needs a new mapper. Accepted:
the spec scopes this feature to a Python seed repository, and a per-runner mapper is the honest shape
for this problem anyway.

---

## D5 — Fix proposer: Anthropic SDK, `claude-opus-5`, structured output, SDK retries off

**Guarantee**: The fixer returns a schema-valid patch, and the run's recorded history is an accurate
account of what was actually spent (Principles VI and the observability rule).

**Mechanism**:

- Model `claude-opus-5` with `thinking={"type": "adaptive"}` and `output_config={"effort": "high"}` —
  diagnosing a failing test from source is exactly the reasoning-heavy work adaptive thinking is for.
- `client.messages.parse(..., output_format=FixProposal)` with a Pydantic model, so the response is a
  validated object rather than prose to be regex-parsed. Schema in
  [contracts/fix_proposal.schema.json](./contracts/fix_proposal.schema.json).
- `max_tokens=16000`, non-streaming — inside the SDK's own HTTP timeout guidance for non-streaming
  requests, and a patch does not need more.
- **`AsyncAnthropic(max_retries=0, timeout=240.0)`.** Temporal's `RetryPolicy` is the only retry
  layer. The SDK's client timeout sits below the activity's 300s `start_to_close_timeout` so the SDK
  raises a clean `APITimeoutError` before Temporal's own timeout fires, which keeps the failure
  attributable in the history.
- Error classification drives the retry policy: `RateLimitError`, `InternalServerError`,
  `APIConnectionError` and `APITimeoutError` propagate and are retried with backoff;
  `BadRequestError`, `AuthenticationError`, `PermissionDeniedError` and `NotFoundError` are wrapped in
  a non-retryable `ApplicationError` and listed in `non_retryable_error_types`, because retrying a
  malformed request or a bad credential only burns the budget.

**Evidence**: SC-003 asks for zero fix-proposal calls attributable to already-finished attempts,
counted from the history. That count is only trustworthy if one history entry equals one billed call —
which is exactly what `max_retries=0` guarantees. With the SDK default of 2, three Temporal attempts
could hide nine calls behind three history entries.

**Trade-off**: Temporal's retry backoff is coarser than the SDK's, and a transient 429 now costs a
whole activity retry rather than a fast in-process one. Accepted: an auditable history is worth more
here than retry latency, and this POC's entire claim rests on that history.

---

## D6 — Run identity and the fix branch

**Guarantee**: One revision cannot produce two concurrent runs (FR-001); branch creation is idempotent
and never destroys a previous result (FR-020).

**Mechanism**: `workflow_id = f"fix-{revision}"`. Temporal's default duplicate policy rejects a start
while a run with that id is open, so the dedup is the engine's, not hand-rolled — the same mechanism
the later webhook phase will rely on. `create_fix_branch` resolves `fix/<revision>`: absent → create;
present and pointing at a commit whose tree matches the winning patch → no-op success; present and
divergent → `BranchConflictError` and the run ends `conflicted`. The branch ref is written directly;
the operator's `HEAD`, index and working tree are never touched.

**Evidence**: Writing the ref rather than checking out is what makes SC-011 (zero change to the
operator's repository state) achievable and testable by comparing `HEAD`, the index and the worktree
before and after.

**Trade-off**: Re-running the demo on the same revision hits the existing branch and ends
`conflicted` instead of overwriting. Deliberate — silent overwrite is the failure mode this guards.
`scripts/demo.sh` deletes the branch first, which is the honest place for that decision to live.

---

## D7 — Determinism enforcement

**Guarantee**: Replay works, therefore resume works (Principle IV).

**Mechanism**: `workflows/fix_workflow.py` imports domain models and activity *stubs* inside
`with workflow.unsafe.imports_passed_through():`, and imports nothing from `adapters/`. No
`datetime.now()`, no `random`, no `uuid` — `workflow.now()` where time is needed. Loop state
(`attempt`, `history`) lives in local variables, which replay reconstructs from the event history.
`tests/replay/test_determinism.py` runs `temporalio.worker.Replayer` against a checked-in history JSON
captured from a real run.

**Evidence**: The replay test fails loudly on any non-deterministic change to workflow code, including
ones that would otherwise only surface as a corrupted resume in the demo — the worst possible moment.

**Trade-off**: The recorded history must be re-captured whenever the workflow's activity sequence
legitimately changes, which is a small chore on purpose: it forces a deliberate decision every time
the shape of the workflow moves.

---

## D8 — Workflow-level testing without real time

**Guarantee**: Timeout and budget behavior is tested without waiting for real timeouts.

**Mechanism**: `temporalio.testing.WorkflowEnvironment.start_time_skipping()` for workflow tests with
fake activities; real Docker and real git in `tests/integration/`; the kill-resume proof stays a
scripted demo (`scripts/demo_kill_resume.sh`) because killing a worker is what it actually tests.

**Trade-off**: The headline guarantee (SC-002) is proven by a script rather than by the unit suite.
Accepted — the point is a reproducible demo, and the script is checked in and versioned.
