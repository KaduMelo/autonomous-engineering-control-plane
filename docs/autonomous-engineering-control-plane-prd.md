# PRD — Autonomous Engineering Control Plane
### Self-Fixing Code Agent · triggered by CI (Level C)

| | |
|---|---|
| **Author** | Kadu (Carlos Eduardo Chessi Melo Silva) |
| **Status** | Technical POC — portfolio / interview preparation (agent orchestration / control plane) |
| **Core stack** | Python · Temporal · LLM agent · Docker |
| **Input** | CI webhook (failing build) |
| **Output** | Pull Request with the validated fix |

---

## 1. Executive Summary

- **Problem:** a red build in CI needs manual triage and fixing. A simple, sequential LLM script that tries to automate this does not survive infrastructure failures (the worker dies, an API is down, the generated code is wrong). It restarts from zero — paying for the same LLM calls again and losing its state.
- **Solution:** a Python control plane built on **Temporal**. When a build fails, it runs an agent that reads the problem, fixes the code, validates the fix with tests in an isolated sandbox, and opens a PR. Execution is **durable** (it survives a worker crash), **isolated** (generated code never runs on the host), and it has a **human gate** before the PR.
- **Success metric (POC):** a reproducible end-to-end demo. Given a repo with a failing test, the system opens a green PR within `max_attempts`; and a run that is interrupted by killing the worker **resumes without redoing** the activities that already finished. This is a POC to demonstrate skill, **not** production — the bar is *mechanism density* (Guarantee → Mechanism → Evidence → Trade-off), not feature coverage.

---

## 2. Problem and Evidence

### Technical problem
Agentic engineering tasks have properties that break the naive approach:

- execution takes minutes to hours → a large window for the worker to die;
- external dependencies (LLM, GitHub) fail in transient ways;
- the agent generates wrong code and must iterate on objective feedback;
- the agent can enter an expensive loop;
- LLM-generated code is **untrusted** and must not run on the orchestrator's host;
- the execution state must survive infrastructure failures.

A Python script with sequential LLM calls solves none of this: it loses state on a crash, has no structured retry/timeout per step, does not isolate execution, and reprocesses from the start when it restarts.

### Market evidence (what the gap filters for)
The target role asks, in its own words: *"Python control plane for a system that writes and fixes its own code… a real workflow engine — Temporal, Step Functions — plus agent systems shipped to production."* The filter is not "an agent that works on my machine" — it is **production-readiness**: isolation, idempotency, failure recovery, observability. That is exactly where most portfolios stop.

> No invented business baseline. This POC's metrics are technical (Section 8).

### Meta-problem (honest)
The secondary — and real — goal is to **demonstrate** strong Python backend and durable orchestration skills, with high mechanism density. A POC that name-drops 8 technologies has low density and is hard to defend. A small, deep POC is the opposite. This criterion drives the whole negative scope in Section 4.

---

## 3. Affected Persona + JTBD

**Primary persona (product):** an engineer / engineering platform that uses the control plane.

> **JTBD:** When a build breaks in CI, I need the system to diagnose it and propose a fix that is already validated by tests, so that I can review a PR instead of debugging from scratch.

**Secondary persona (real context):** the builder, demonstrating senior-level Python backend and agent orchestration skills to technical reviewers.

---

## 4. Proposed Solution

### What we do

Level C flow — from the red build to the PR:

```text
CI build fails
      │  (webhook)
      ▼
Ingress ──► start_workflow(id = fix-<commit_sha>)   # native dedup
      │
      ▼
┌──────────────── EngineeringWorkflow (deterministic) ─────────────────┐
│  analyze_repo                                                        │
│       │                                                              │
│       ▼                                                              │
│  ┌── loop (bounded: max_attempts) ─────────────────────────────┐     │
│  │  propose_fix (LLM)                                           │     │
│  │       ▼                                                      │     │
│  │  apply_patch (idempotent, clean base)                        │     │
│  │       ▼                                                      │     │
│  │  run_tests (Docker isolated, heartbeat, timeout)             │     │
│  │       │                                                      │     │
│  │   PASS ┴ FAIL ── feedback ──► next iteration                 │     │
│  └───────────────────────────────────────────────────────────── ┘    │
│       │ (PASS)                                                       │
│       ▼                                                              │
│  wait: approval signal  ⟂  SLA timer (48h)                          │
│       │ (approved)                                                   │
│       ▼                                                              │
│  open_pr (idempotent, deterministic branch) ──► PR url               │
└──────────────────────────────────────────────────────────────────────┘
```

Separation of responsibilities:

- **Workflow (deterministic):** the loop, the attempt counter, the pass/fail decision, and the signal-vs-timer race. **No I/O.**
- **Activities (non-deterministic, retryable):** `analyze_repo`, `propose_fix`, `apply_patch`, `run_tests`, `open_pr`, `report_failure`. All I/O and non-determinism live here.

We apply **hexagonal** architecture with restraint: we keep the seams (the port boundaries `LLMPort`, `TestRunnerPort`) for decoupling and testability, but we do not build every adapter on day one.

### What we do NOT do (negative scope)

| Out of scope | Why |
|---|---|
| **Circuit breaker** | Temporal's retry policy + `max_attempts` already cover the essentials. An in-process breaker does not survive a restart and is per-worker. It becomes a **trade-off discussion**, not code. |
| **LangGraph as the loop owner** | Temporal **is** the loop (the control plane). If LangGraph is added, it lives inside a single activity — and then durability is per-activity, not per-step. |
| **continue-as-new** | Attempts are *bounded* → the event history stays small. There is no long-running workflow here. |
| **MCP** | Orthogonal plumbing. It demonstrates nothing that is Temporal-specific. |
| **Full set of hexagonal adapters** | Only the seams we need. Full ceremony would slow down the skeleton. |
| **HTTP API / frontend** | The entry point is the ingress webhook + the approval endpoint. Nothing more. |
| **Multi-repo watcher** | We choose *start-per-build* (see trade-offs). |
| **OpenTelemetry spans per activity** | The Temporal event history is already the system of record, and §6 says so. A second telemetry path adds a dependency without adding a guarantee the history does not already give. |
| **A second LLM provider adapter** | The port is the demonstration. Building a second adapter behind it proves nothing the port does not already prove, and doubles the surface to test. |
| **Status query handler** | The Temporal Web UI already answers "what is this run doing". A query handler would be a second read path over state the history already holds. |
| **Logging to an external channel on exhaustion** | The `exhausted` outcome is already explicit in the run record. Shipping it elsewhere is integration plumbing, not mechanism. |

### Conscious trade-offs

- **Start-per-build > watcher-per-repo:** we choose simplicity and per-run isolation, and give up per-repo dedup, serialization, and memory across builds.
- **Temporal owns the loop > LangGraph:** we get durable per-step checkpointing and full observability in the UI, and give up LangGraph's graph ergonomics.
- **Docker per run > running on the host:** we accept spin-up overhead to contain the blast radius of arbitrary code.
- **Depth per component > breadth of technologies:** a direct fix for the risk of low mechanism density.

---

## 5. Functional Requirements

Priority: **MUST** (no POC without it) · **SHOULD** (production shape).

> There is no COULD tier. The items that used to sit there - status query and
> external-channel logging - were cut with Phase 3 and are recorded in the
> negative scope in §4, with the reason.

| # | User Story | Prio |
|---|---|---|
| US1 | As a CI platform, I want to trigger the control plane through a webhook when a build fails, so that the fix starts without manual work. | **MUST** |
| US2 | As the control plane, I want to deduplicate triggers by commit (`id = fix-<sha>`), so that repeated webhooks do not open duplicate runs. | **MUST** |
| US3 | As the agent, I want to extract context from the repo and the failing test, so that I have a basis to propose a fix. | **MUST** |
| US4 | As the control plane, I want to iterate propose→apply→test with an attempt limit, so that the agent converges without an infinite loop. | **MUST** |
| US5 | As the control plane, I want to validate each attempt with tests in an isolated environment, so that the feedback is objective and the host stays protected. | **MUST** |
| US6 | As the control plane, I want an interrupted run to resume from the saved point, so that an infra failure does not restart the work or pay for the same LLM calls again. | **MUST** |
| US7 | As a reviewer, I want to approve (or reject) the fix before the PR, with SLA escalation, so that there is human governance. | **SHOULD** |
| US8 | As the agent, I want to open an idempotent PR at the end, so that a retry does not create duplicate PRs. | **SHOULD** |

---

## 6. Edge Cases and Non-Functional Requirements

### Edge cases (expected behavior)

| Scenario | Behavior |
|---|---|
| A legitimately failing test | `run_tests` returns `TestResult(passed=False)` — this is a **result, not an exception**. It becomes feedback for the next `propose_fix`. |
| The runner broke (Docker died, setup failed) | `run_tests` **raises an exception** → Temporal retries the activity. |
| Duplicate webhook | `id = fix-<sha>` → `WorkflowAlreadyStarted`, no new run. |
| `apply_patch` retry | Idempotent: always from a clean base (`checkout <sha>` + `apply`). It never stacks the diff. |
| `open_pr` retry | Idempotent: deterministic branch + check-if-exists. It never opens 2 PRs. |
| The worker dies mid-run | The workflow resumes by replaying the event history; finished activities do not run again. |
| The LLM enters an expensive loop | `max_attempts` + per-activity timeout limit the damage. |
| The test container hangs | The `heartbeat_timeout` detects it and fails the activity. |
| Approval does not arrive within 48h | The SLA timer fires → policy (auto-reject or escalate). |
| LLM/GitHub unavailable (transient) | Retry with exponential backoff (the activity's retry policy). |

### Non-functional

- **Isolation:** LLM-generated code never runs on the control plane host — a disposable container, an isolated filesystem, and a timeout.
- **Idempotency:** every activity with a side effect (`apply_patch`, `open_pr`) is safe to re-run.
- **Determinism:** workflow code has no I/O; activity imports go through `imports_passed_through`.
- **Observability:** the Temporal event history is the source of truth. OpenTelemetry spans are **out of scope** (§4) - the history already answers what happened, and a second telemetry path would add a dependency without adding a guarantee.
- **Performance:** latency is dominated by the LLM + the test suite; all timeouts are explicit (start-to-close + heartbeat). There is no latency SLA for the POC.
- **Security:** LLM/GitHub credentials stay out of the workflow (only in activities), with a minimum-scope token. LGPD **[N/A — local POC, no personal data]**.

---

## 7. Acceptance Criteria

Format: **GIVEN / WHEN / THEN** (pass/fail).

- **US1/US2** — GIVEN a webhook for a failed build with a `commit_sha`, WHEN the ingress receives it, THEN a workflow with `id = fix-<sha>` starts; AND a second webhook with the same sha does **not** create a new run.
- **US4** — GIVEN a repo with a failing test, WHEN the loop runs, THEN it runs at most `max_attempts` iterations and stops at the first green result.
- **US5** — GIVEN a fix attempt, WHEN `run_tests` runs, THEN it runs in an isolated container with a timeout; AND a failing test returns a result (not an exception); AND a broken runner triggers a retry.
- **US6 (core demo)** — GIVEN a run on attempt 3 of 5, WHEN the worker is killed and another one starts, THEN the workflow resumes on attempt 3; AND the finished activities do **not** run again (0 LLM re-calls for earlier attempts).
- **US7** — GIVEN a green fix waiting for approval, WHEN an approval signal arrives, THEN the flow continues to `open_pr`; AND if no signal arrives within 48h, THEN the SLA timer fires the defined policy.
- **US8** — GIVEN an approved fix, WHEN `open_pr` runs and is retried, THEN there is exactly 1 PR (deterministic branch).

---

## 8. Success Metrics (POC — technical)

> No invented business KPI. Baselines to be defined after seeding the repos.

| Metric | Baseline | Target | How we measure |
|---|---|---|---|
| **Resolution rate** — % of seed repos that reach a green PR within `max_attempts` | **[TBD]** | **[TBD]** | demo script over the seed set |
| **Durability** — runs interrupted by a worker kill that resume without reprocessing | — | 100% | event history (Temporal UI) |
| **Cost on resume** — LLM re-calls for attempts that already finished, after a resume | — | 0 | count of `propose_fix` invocations in the history |
| **Defensibility** — components with an articulated Guarantee→Mechanism→Evidence→Trade-off | — | 100% | own checklist |

**Tool / frequency / owner:** Temporal Web UI + a reproducible demo script; measured on each POC iteration; owner: Kadu.

---

## 9. Risks and Mitigations

| Risk | Type | Prob. | Impact | Mitigation |
|---|---|---|---|---|
| Stacking technologies again (over-scope) | Technical | Medium | High | Negative scope locked (§4); build the loop skeleton before the trigger/PR |
| `run_tests` confusing a red result with an activity failure | Technical | Medium | High | Explicit result-vs-exception contract + tests for the runner itself |
| Non-determinism leaking into the workflow | Technical | Medium | High | I/O only in activities; `imports_passed_through`; a replay test |
| Weak isolation (arbitrary code on the host) | Security | Low | High | Disposable container + isolated fs + timeout |
| Broken idempotency (duplicate PR/patch) | Technical | Medium | Medium | Deterministic branch, clean base, check-if-exists |
| Demo not reproducible in the interview | Portfolio | Medium | High | Versioned seed repo + demo script + a recorded kill-resume run |

---

## 10. Timeline and Dependencies

Phases (rough effort; incremental delivery inside the C scope). Three phases, and
Phase 2 closes the scope — there is no optional fourth. A "depth" phase holding
OTel, LangGraph, MCP, multi-provider adapters and a status query was cut: it
contradicted §4, which already rejected LangGraph and MCP, and it was the shape
the over-scope risk in §9 takes in practice.

| Phase | Scope | Completion milestone | Effort |
|---|---|---|---|
| **0 — Durable loop** | `analyze_repo` + the propose→apply→`run_tests` loop (isolated) + resume. No gate, no PR. | Red repo → green end-to-end; **worker kill → resume** works | ~2–4 days |
| **1 — Governance** | Approval signal + SLA timer + `report_failure` | Human gate works; SLA fires | +days |
| **2 — Trigger + PR (closes C)** | Ingress webhook (start-per-build, dedup) + idempotent `open_pr` | **Failed build → green PR** | +days |

**Delivery order: 0 → 2 → 1.** Phase 2 carries the last two **MUST** items (US1, US2)
and closes the declared scope; Phase 1 is entirely **SHOULD** (US7 alone). Building a
SHOULD before two MUSTs is priority inversion, and the §5 table says so plainly now that
there is no COULD tier to blur it. Phase 2 is also cheaper than this table suggests: US2's
mechanism already ships — `workflow_id = fix-<sha>` makes Temporal itself reject the
duplicate — and `open_pr` reuses the deterministic branch `create_fix_branch` already
writes, so what is left is a push and a PR call. The phase *numbers* are kept as
identities because other documents reference them.

**Implementation order (inside Phase 0):** `run_tests` (the objective feedback is the foundation of everything) → `propose_fix` → `apply_patch` → the end-to-end loop without the gate.

**Dependencies:**
- A local Temporal server (`temporal server start-dev` / docker)
- An LLM provider + credential
- A Docker runtime on the worker host
- A seed repo with a **deterministic** failing test

---

## Quality Checklist

- [x] Executive summary understandable in 30s
- [x] Problem backed by evidence (target JD + technical properties), no invented metric
- [x] Negative scope made explicit
- [x] Metrics with a **[TBD]** baseline and a target — never guessed
- [x] Edge cases covered (result-vs-exception, dedup, idempotency, resume)
- [x] Risks with mitigations