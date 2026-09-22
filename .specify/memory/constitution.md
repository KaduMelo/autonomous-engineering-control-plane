<!--
SYNC IMPACT REPORT
Version change: (template, unversioned) → 1.0.0
Bump rationale: MAJOR — initial ratification; all principles defined for the first time.

Principles defined (all new):
  I.   Durable Execution by Default
  II.  Untrusted Code Never Touches the Host
  III. Idempotent Side Effects
  IV.  Deterministic Workflows, Impure Activities
  V.   Result vs. Exception (NON-NEGOTIABLE)
  VI.  Bounded Cost and Bounded Time
  VII. Mechanism Density over Feature Count

Sections added:
  - Core Principles
  - Technology and Scope Constraints (locked negative scope)
  - Development Workflow and Quality Gates
  - Governance

Sections removed: none (template placeholders replaced)

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — generic "Constitution Check" gate reads this file; no edit needed
  ✅ .specify/templates/spec-template.md — no constitution-specific mandatory section added
  ✅ .specify/templates/tasks-template.md — principle-driven task types (isolation, idempotency, replay) are
     expressible under existing categories; no edit needed
  ✅ CLAUDE.md — spec-kit managed block, unchanged
  ⚠ docs/autonomous-engineering-control-plane-prd.md — source of truth for scope; kept as-is (PRD, not governance)

Deferred TODOs: none
-->

# Autonomous Engineering Control Plane Constitution

## Core Principles

### I. Durable Execution by Default

Any unit of work that spans more than one external call MUST run as a Temporal workflow whose state
survives process death. A run interrupted at attempt N MUST resume at attempt N on a fresh worker,
and activities that already completed MUST NOT execute again.

Rationale: the whole point of this control plane over a sequential LLM script is that infrastructure
failure costs zero re-work and zero duplicate LLM spend. A feature that cannot demonstrate
kill-and-resume is not done.

### II. Untrusted Code Never Touches the Host

Code produced by the LLM MUST execute only inside a disposable container with an isolated filesystem
and an enforced wall-clock timeout. The orchestrator host, the worker process, and the repository
working copy of the control plane itself MUST be unreachable from that sandbox. No credential
required by the control plane may be present in the sandbox environment.

Rationale: generated code is adversarial input. Isolation is a security boundary, not a convenience.

### III. Idempotent Side Effects

Every activity with an external side effect (`apply_patch`, `open_pr`, and any future writer) MUST be
safe to execute twice with the same input and produce the same end state. Concretely: `apply_patch`
MUST start from a clean checkout of the target commit rather than stacking onto prior state, and
`open_pr` MUST use a deterministic branch name plus a check-if-exists before creating.

Rationale: Temporal retries activities. Non-idempotent writers turn a transient network error into
duplicate pull requests or a corrupted patch.

### IV. Deterministic Workflows, Impure Activities

Workflow code MUST contain no I/O, no wall-clock reads, no randomness, and no non-deterministic
imports; all of it belongs in activities, with third-party imports routed through
`imports_passed_through`. Every workflow MUST have a replay test against a recorded event history
that fails when determinism is broken.

Rationale: replay is what makes resume possible. A single non-deterministic call in workflow code
silently destroys Principle I.

### V. Result vs. Exception (NON-NEGOTIABLE)

A legitimately failing test is a **result** — `TestResult(passed=False)` — and becomes feedback for
the next iteration. A broken runner (Docker unavailable, setup failed, image missing) is an
**exception** and MUST propagate so Temporal retries it. These two paths MUST never be collapsed,
and the distinction MUST be covered by tests on the runner itself.

Rationale: confusing the two either burns retries on code that is simply wrong, or silently accepts
infrastructure failure as an agent verdict. This is the single highest-risk contract in the system.

### VI. Bounded Cost and Bounded Time

Every agent loop MUST have an explicit `max_attempts`. Every activity MUST declare an explicit
start-to-close timeout, and any activity that can block on an external process MUST heartbeat with a
`heartbeat_timeout`. Every human gate MUST have an SLA timer with a defined expiry policy. No
unbounded wait and no unbounded loop may be merged.

Rationale: an LLM agent with no ceiling is an open-ended bill and an open-ended hang.

### VII. Mechanism Density over Feature Count

Each component MUST be defensible as Guarantee → Mechanism → Evidence → Trade-off. Adding a
technology is justified only by a guarantee that the current stack cannot provide. When a choice is
between one more integration and one more level of depth on an existing mechanism, depth wins.

Rationale: this is a POC judged by technical reviewers on the strength of its mechanisms, not on
how many logos it stacks. Breadth without depth is the failure mode being designed against.

## Technology and Scope Constraints

**Fixed stack:** Python, Temporal (durable execution), an LLM provider behind a port/interface,
Docker (sandboxed test execution), GitHub (trigger in, PR out).

**Locked negative scope** — the following are out of scope and MUST NOT be introduced without a
constitution amendment: multi-tenant deployment, a production UI, a hosted/managed Temporal cluster,
autonomous merge without a human gate, support for arbitrary repository languages beyond the seed
set, and any fine-tuning or model training.

**Credential boundary:** LLM and GitHub credentials live only in activity execution context, never in
workflow code and never in the sandbox. Tokens MUST be minimum-scope.

**Observability:** the Temporal event history is the source of truth for what happened. Any
additional telemetry is additive and MUST NOT become a prerequisite for debugging a run.

**Data:** the POC handles no personal data. Any change that introduces it requires an amendment.

## Development Workflow and Quality Gates

**Spec-driven flow.** Work proceeds through the spec-kit cycle — constitution → specify → (clarify) →
plan → tasks → (analyze/checklist) → implement — one feature per PRD phase. Implementation MUST NOT
begin before `tasks.md` exists for that feature.

**Constitution Check.** Every `plan.md` MUST evaluate the design against these principles before
Phase 0 research and again after design. A violation MUST be either removed or recorded in the
Complexity Tracking table with the simpler alternative that was rejected and why.

**Definition of done for any feature touching the agent loop:**

1. The acceptance criteria in the feature spec pass as written (GIVEN/WHEN/THEN).
2. A replay test proves workflow determinism (Principle IV).
3. A kill-and-resume run proves durability with zero repeated LLM calls (Principle I).
4. Idempotent activities are proven by executing them twice in a test (Principle III).
5. The result-vs-exception contract is covered by runner tests (Principle V).

**Reproducibility.** The demo MUST be runnable from a versioned seed repository with a deterministic
failing test, driven by a checked-in script. "Works on my machine" is not evidence.

## Governance

This constitution supersedes ad-hoc practice. When a spec, plan, or task conflicts with it, the
constitution wins and the conflicting artifact is corrected.

**Amendments** require: (a) editing this file, (b) a version bump per the policy below, (c) an
updated Sync Impact Report at the top of this file, and (d) propagation to any dependent artifact
that the change invalidates.

**Versioning policy** (semantic):

- **MAJOR** — a principle is removed or redefined in a backward-incompatible way, or the locked
  negative scope is opened.
- **MINOR** — a new principle or section is added, or existing guidance is materially expanded.
- **PATCH** — clarification, wording, or typo fixes with no change in obligation.

**Compliance review.** Every plan runs the Constitution Check gate. Any principle marked
NON-NEGOTIABLE may not be waived through Complexity Tracking — it requires an amendment.

**Runtime guidance.** `CLAUDE.md` and the active feature's `plan.md` carry operational context
(stack details, structure, commands). This file carries only non-negotiable rules.

**Version**: 1.0.0 | **Ratified**: 2026-09-22 | **Last Amended**: 2026-09-22
