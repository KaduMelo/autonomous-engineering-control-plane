# Implementation Plan: CI Trigger and Pull Request

**Branch**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-ci-trigger-and-pr/spec.md`

## Summary

Two ends bolted onto the loop feature 001 already built. At the front, an HTTP ingress receives a
signed build-failure notification and starts a run for that commit. At the back, a run that reaches
green pushes its fix branch and opens exactly one pull request.

The load-bearing choice is that **deduplication is not code we write**. `workflow_id = fix-<sha>`
plus `WorkflowIDReusePolicy.REJECT_DUPLICATE` makes the durable execution engine itself refuse a
second run for a commit — whether the first is still running (FR-004) or already finished
(FR-005a). The ingress catches the refusal and reports the earlier outcome. No dedup table, no
cursor, no lock.

Everything runs offline. The repository host is reached through a port with two adapters, and only
the local one is ever exercised.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: adds `starlette` + `uvicorn` (the ingress) and declares `httpx2`
(already present transitively via `anthropic`). Existing: `temporalio`, `anthropic`, `pydantic`.

**Storage**: None new. The Temporal event history remains the system of record. A content-addressed
clone cache on disk is an optimization, not state — a miss is a re-clone.

**Testing**: `pytest`, plus an in-process ASGI stand-in for the repository host and a bare git
repository as the remote. `SC-010` requires the whole suite to pass with no network.

**Target Platform**: Linux host with Docker and a local Temporal dev server. The ingress is a
separate process from the worker.

**Project Type**: Single Python project — worker, ingress, CLI.

**Performance Goals**: The ingress responds without waiting for the run (FR-006). Target is well
under a webhook sender's usual 10s delivery timeout; measured, not assumed.

**Constraints**: No network, no token, no CI installation required to run anything. Credentials for
the host stay in activities. Signature verification is constant-time.

**Scale/Scope**: One repository, one notification at a time. 2 new activities, 1 ingress, ~3 new
adapters.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | How this design satisfies it | Status |
|---|---|---|---|
| I. Durable Execution by Default | The trigger must not become a second, fragile source of truth | The ingress starts a workflow and returns. Everything durable stays in the engine; the ingress holds no state to lose | PASS |
| II. Untrusted Code Never Touches the Host | New inputs arrive from outside | The notification is untrusted input: HMAC-verified before anything is parsed for meaning, and the cloned repository is only ever executed inside the existing sandbox | PASS |
| III. Idempotent Side Effects | Two new writers: the push and the pull request | Push is to a deterministic branch already written idempotently by 001. The pull request is check-if-exists before create, and treats the host's "already exists" as success | PASS |
| IV. Deterministic Workflows, Impure Activities | The workflow gains a step | `open_pr` is an activity like the rest; the workflow only sequences it. The replay test is extended to a history that includes it | PASS |
| V. Result vs. Exception (NON-NEGOTIABLE) | New failure modes must not blur | A clone failure, a push rejection and a host 5xx are **errors** (retried). A pull request that already exists is a **result** (no-op success). Neither is ever reported as a fix that failed | PASS |
| VI. Bounded Cost and Bounded Time | New I/O paths | Explicit timeouts on clone, push and the host call; the ingress never blocks on the run; `REJECT_DUPLICATE` bounds the number of runs per commit at one | PASS |
| VII. Mechanism Density over Feature Count | Two new dependencies | `starlette`+`uvicorn` because the ingress must be async to call the Temporal client without blocking. `httpx2` is declared rather than added — it is already installed via `anthropic`, so adding `httpx` would mean a second HTTP stack for no gain. FastAPI was rejected: its differentiators are unused here | PASS |

**Locked negative scope respected**: no multi-tenancy, no UI, no hosted cluster, and **no merging**
— FR-013 forbids it, so "autonomous merge without a human gate" stays untouched. The approval gate
is PRD Phase 1, delivered next.

**Post-design re-check (after Phase 1)**: PASS. The one decision that adds work rather than removing
it — a content-addressed clone cache instead of cloning inside every activity — is recorded in
Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/002-ci-trigger-and-pr/
├── plan.md              # This file
├── research.md          # Phase 0 output — decisions with alternatives rejected
├── data-model.md        # Phase 1 output — new entities and the ingress state machine
├── quickstart.md        # Phase 1 output — the offline end-to-end demo
├── contracts/
│   ├── ingress.md       # The HTTP contract: request, signature, status codes
│   ├── activities.md    # clone_repo and open_pr
│   └── host_api.md      # The subset of the repository host's API this feature uses
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
src/control_plane/
├── ingress/
│   ├── app.py                # Starlette app: one POST route
│   ├── signature.py          # constant-time HMAC verification
│   └── __main__.py           # `python -m control_plane.ingress`
├── activities/
│   ├── clone.py              # NEW: ensure_clone — content-addressed, idempotent
│   └── pull_request.py       # NEW: open_pr — push, then check-if-exists, then create
├── adapters/
│   ├── repo.py               # EXTENDED: clone, fetch, push
│   └── host.py               # NEW: RepositoryHostPort + GitHubHost + the local stand-in
├── domain/
│   └── models.py             # EXTENDED: BuildNotification, IngressOutcome, PullRequestRef
└── workflows/
    └── fix_workflow.py       # EXTENDED: open_pr after create_fix_branch

tests/
├── unit/                     # signature verification, payload parsing, PR body rendering
├── integration/              # ingress against a real engine, push to a bare repo, host stand-in
└── replay/histories/         # re-recorded: the history now includes open_pr

scripts/
├── fake_host.py              # NEW: the local stand-in, runnable for the demo
└── demo_webhook.sh           # NEW: signed payload -> ingress -> run -> pull request
```

**Structure Decision**: The ingress gets its own package rather than living beside the worker,
because it is a separate process with a different failure mode: the worker's failures are visible in
the run history, and the ingress's are not — a rejected notification produces no run at all. That
asymmetry is why FR-015 exists, and keeping the code separate keeps it obvious.

`adapters/host.py` follows the shape already set by `adapters/fixer.py`: a port plus a real adapter
plus a stand-in the tests drive. The stand-in is not a mock inside the test suite — it is a runnable
process (`scripts/fake_host.py`), so the demo exercises the same HTTP path the real adapter would.

## Complexity Tracking

| Decision | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| A content-addressed clone cache keyed by `(source, revision)`, rather than cloning inside every activity that needs the repository | Activities stay pure functions of their inputs — any activity can reconstitute the clone from the same key, and a cache miss is just a re-clone. Nothing has to survive a worker dying (Principles I, III) | Cloning inside each of the four activities that read the repository is simpler and stateless, but it is four clones per attempt against a remote. Passing a clone path between activities is what Principle I forbids — it is the exact failure feature 001's design note describes |
| `starlette` + `uvicorn` added for a single endpoint | The ingress must call the Temporal client, which is async, and must return before the run finishes (FR-006). Hand-rolling an asyncio HTTP server is more code and more risk for no guarantee gained | `http.server` is stdlib and adds nothing, but it is synchronous — it would need a thread or an asyncio bridge per request. FastAPI was also rejected: it adds a layer whose value here (auto docs, DI, path params) is entirely unused, and pydantic already covers validation |
