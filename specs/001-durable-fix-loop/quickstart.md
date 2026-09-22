# Quickstart: Durable Self-Fixing Agent Loop

**Feature**: `001-durable-fix-loop` | **Date**: 2026-09-22

Two things to prove, in order: a red repository reaches green, and a run that is killed mid-flight
resumes without paying twice. The second one is the demo the whole POC exists for.

## Prerequisites

| Requirement | Check |
|---|---|
| Python 3.12 | `python3 --version` |
| Docker runtime | `docker info` |
| Temporal CLI | `temporal --version` |
| Anthropic credential | `ant auth status`, or `ANTHROPIC_API_KEY` exported |

## One-time setup

```bash
uv sync                                  # or: pip install -e ".[dev]"
docker build -t fixloop-sandbox:latest sandbox/
```

The image is built **once, with network**, and carries every dependency the seed suite needs. At run
time the container has no network at all — that is the clarified isolation decision, and a dependency
missing from the image surfaces as an execution error rather than being silently fetched.

## Run it end to end

Three terminals.

```bash
# 1 — durable execution engine
temporal server start-dev

# 2 — the worker (workflow + activities)
python -m control_plane.worker

# 3 — start a run
./scripts/demo.sh
```

`scripts/demo.sh` resets the seed repository to its red revision, deletes any leftover `fix/<sha>`
branch from an earlier run, and starts the workflow.

**Expected**: the baseline validation comes back red, the loop iterates, and within `max_attempts` the
run ends `fixed`. Then:

```bash
git -C seed/broken-calculator log --oneline fix/<sha> -1
git -C seed/broken-calculator status          # unchanged — SC-011
```

The branch exists and your working tree is exactly as you left it.

> **Why the script deletes the branch first**: re-running on the same revision finds the existing
> `fix/<sha>` and ends `conflicted` rather than overwriting it (FR-020). That is deliberate — a
> control plane that silently overwrites a previous result is the failure this guards against. The
> deletion belongs in the demo script, where it is a visible choice.

## The kill-resume demo (SC-002, SC-003)

```bash
./scripts/demo_kill_resume.sh
```

The script starts a run, waits until the history shows attempt 3 in flight, kills the worker process,
and starts a fresh one.

**Expected**: the run resumes at attempt 3. Open the Temporal Web UI at <http://localhost:8233>, find
`fix-<sha>`, and read the event history:

- `ActivityTaskCompleted` for attempts 1 and 2 appear **once each**. They are not re-executed.
- `propose_fix` appears exactly as many times as there were attempts — no duplicates for the attempts
  that had already finished. That count is SC-003, and it is trustworthy only because the Anthropic
  client runs with `max_retries=0`, so one history entry equals one billed call.
- The run completes normally on the new worker.

## Verification checklist

| Criterion | How to check |
|---|---|
| SC-001 red → green | `scripts/demo.sh` ends `fixed` |
| SC-002 resume | `scripts/demo_kill_resume.sh` completes on the replacement worker |
| SC-003 zero re-calls | count `propose_fix` entries in the history; equals attempt count |
| SC-004 isolation | `pytest tests/integration/test_sandbox_isolation.py` — asserts no network and no host writes |
| SC-005 result vs. exception | `pytest tests/unit/test_exit_code_contract.py` |
| SC-006 idempotent apply | `pytest tests/unit/test_apply_patch_idempotent.py` |
| SC-008 replay | `pytest tests/replay/test_determinism.py` |
| SC-010 one branch | `pytest tests/integration/test_branch_idempotency.py` |
| SC-011 repo untouched | `pytest tests/integration/test_operator_repo_untouched.py` |

```bash
pytest                                   # everything except the two scripted demos
```

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Run ends `conflicted` immediately | `fix/<sha>` left over from a previous run | `git -C seed/broken-calculator branch -D fix/<sha>` |
| Every `run_tests` raises | Docker not running, or the image was never built | `docker info`; rebuild the image |
| `TestRunnerError` on exit code 5 | No tests collected — a patch deleted the test file | Expected behavior; the attempt is an error, not a red result (Principle V) |
| Suite fails on a missing import | Dependency absent from the prebuilt image | Add it to `sandbox/Dockerfile` and rebuild; it cannot be fetched at run time by design |
| Worker starts but nothing happens | Worker on a different task queue than the CLI | Both read `control_plane.config` — check it is the same process env |
