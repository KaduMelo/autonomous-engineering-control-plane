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
temporal server start-dev          # terminal 1
./scripts/demo_kill_resume.sh      # terminal 2 - starts and kills its own workers
```

The script starts a run, waits until the history shows the configured attempt in
flight, kills the worker, starts a replacement, and then **checks that it proved
something**:

```text
distinct workers    2
  -> the run was served by more than one worker: it survived a restart

SC-003: propose_fix scheduled 3 time(s).

PROVEN: 2 workers served this run - it resumed after the kill,
        and propose_fix was scheduled once per attempt, never re-billed.
```

If the kill lands after the run has already finished, the script exits `2` with
`INCONCLUSIVE` rather than reporting success. A demo that can pass without
demonstrating the thing is worse than no demo.

### It does not call the model

`scripts/worker_scripted.py` swaps the `FixerPort` for a script - two wrong
answers, then the right one. What is under test is durability, not the model,
and billing real tokens to demonstrate something unrelated to them is waste.

To run the same flow against Claude, use the real worker and start the run
yourself:

```bash
python -m control_plane.worker                                   # terminal 2
python -m control_plane.cli run --repo .workspaces/seed --sha <sha>   # terminal 3
```

The scripted fixer sleeps `FIXER_DELAY_S` (default 8s) per proposal, standing in
for real model latency. Without it the whole run finishes in about five seconds,
the kill lands after the fact, and the demo reports `INCONCLUSIVE`.

### Reading the evidence yourself

```bash
python scripts/inspect_history.py fix-<sha>                       # the narrative
python scripts/inspect_history.py fix-<sha> --count propose_fix   # just the number
```

**Why that count is trustworthy.** `AnthropicFixer` runs with `max_retries=0`,
so one scheduled activity is one billed call. With the SDK's default of two
retries, three Temporal attempts could hide nine calls behind three history
entries and this number would mean nothing.

**Pass `--run-id`** when re-running a demo on the same revision. The workflow id
is `fix-<sha>` by design, so it points at whichever run is latest - without the
run id you may be reading the previous run's history. The CLI prints the run id
as its first line.

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
