# Feature Specification: Durable Self-Fixing Agent Loop

**Feature Branch**: `001-durable-fix-loop`

**Created**: 2026-09-22

**Status**: Draft

**Input**: PRD Phase 0 — "Durable loop": `analyze_repo` + the propose→apply→`run_tests` loop (isolated) + resume. No human gate, no pull request. Completion milestone: a red repository reaches green end-to-end, and a worker kill mid-run resumes without redoing finished work.

## Clarifications

### Session 2026-09-22

- Q: Does this feature include a trigger that watches for red repositories? → A: No — runs are started
  manually by an operator. The CI webhook ingress and its per-revision deduplication remain out of
  scope (PRD Phase 2).
- Q: What does the operator hand over, and who determines which tests failed? → A: A local filesystem
  path to the repository plus the exact revision. The system discovers the failing tests itself by
  executing the suite once before the first attempt; the operator does not name them.
- Q: How is a candidate change represented? → A: As a patch (unified diff) against the target
  revision, applied to a clean checkout of that revision.
- Q: Does the isolated validation environment have network access? → A: No network access at all.
  Dependencies are baked into a prebuilt environment image ahead of the run.
- Q: May the fixer modify test files? → A: Yes, without restriction. The consequence is recorded in
  Edge Cases and FR-018: a green verdict alone is therefore not proof of a genuine fix, and the
  recorded diff is what a reviewer must inspect.
- Q: What does a successful run leave behind in the target repository? → A: A deterministic branch
  named after the target revision, carrying the winning patch as a commit, created without disturbing
  the operator's checked-out branch or working tree. Re-creating it is a no-op when it already carries
  the same patch, and a recorded conflict when it carries something else — never a silent overwrite.
  (Raised by the operator after the five-question clarification cycle closed.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Objective validation of a candidate fix (Priority: P1)

An operator points the control plane at a repository whose test suite is red. Before any fix is even
proposed, the system must be able to answer one question honestly: *does this code pass its tests?*
It answers by running the suite in a throwaway isolated environment and reporting back a structured
verdict — passed or failed, with the captured failure output — without the tested code ever touching
the machine that runs the control plane.

**Why this priority**: every later step consumes this verdict as its feedback signal. A loop built on
an unreliable or unsafe validator is worthless, and the one contract most likely to be gotten wrong
(a failing test is a *result*; a broken runner is an *error*) lives entirely here. This is the
foundation and is built first.

**Independent Test**: point the validator at the seed repository and at a known-green repository; it
returns `failed` with captured output for the first and `passed` for the second, and in both cases no
file outside the disposable environment is modified. Then break the execution environment
deliberately (remove the runtime, poison the image) and confirm the validator signals an
infrastructure error rather than reporting "tests failed".

**Acceptance Scenarios**:

1. **Given** a repository with a deterministically failing test, **When** validation runs, **Then**
   the system returns a result marked as not-passed carrying the failure output, and does not raise
   an error.
2. **Given** a repository whose tests all pass, **When** validation runs, **Then** the system returns
   a result marked as passed.
3. **Given** an unavailable or broken execution environment, **When** validation is attempted,
   **Then** the system raises an infrastructure error (distinct from a not-passed result) and the
   attempt is retried automatically.
4. **Given** a test suite that hangs and never terminates, **When** the configured execution time
   limit elapses, **Then** the attempt is aborted and reported as an infrastructure error rather than
   hanging the run indefinitely.
5. **Given** any validation run, **When** it completes or aborts, **Then** the disposable environment
   is destroyed and no artifact from the tested code remains on the host.
6. **Given** tested code that attempts to reach the network, **When** validation runs, **Then** the
   attempt fails because the environment has no network access; all dependencies the suite needs are
   already present in the prebuilt environment image.

---

### User Story 2 - Iterating to a green fix within a bounded budget (Priority: P1)

Given a red repository, the system first runs the suite itself to discover which tests fail, gathers
the context a fixer needs (those failing tests, the relevant source, the failure output), proposes a
change as a patch against the target revision, applies it to a clean checkout, and re-validates. If the change does not
make the suite green, the failure output from that attempt feeds the next proposal. The cycle repeats
until the suite is green or the attempt budget is exhausted.

**Why this priority**: this is the feature's core value — a red repository becoming green with no
human in the loop. It is P1 alongside US1 because together they are the minimum that delivers the
promised outcome.

**Independent Test**: run the loop against the seed repository and observe it reach a green verdict
within the configured attempt budget; then run it against a repository whose failure is unfixable by
the fixer and observe it stop exactly at the budget, reporting exhaustion rather than looping.

**Acceptance Scenarios**:

1. **Given** a red repository, **When** the loop runs, **Then** it performs at most the configured
   maximum number of attempts and stops at the first green verdict.
2. **Given** an attempt whose validation came back not-passed, **When** the next proposal is made,
   **Then** that attempt's failure output is part of the context used to produce it.
3. **Given** the attempt budget is exhausted without a green verdict, **When** the loop ends, **Then**
   the run terminates with an explicit exhausted outcome recording every attempt made.
4. **Given** a proposed change is applied and later retried, **When** it is applied a second time,
   **Then** the resulting working tree is identical to the first application — changes never stack on
   top of one another.
5. **Given** a proposed change that cannot be applied to the target revision, **When** application is
   attempted, **Then** the attempt is recorded as failed and the loop continues to the next attempt
   rather than aborting the whole run.
6. **Given** an attempt whose validation came back green, **When** the run finishes, **Then** the
   target repository contains a branch named deterministically after the target revision, pointing at
   a commit that applies the winning patch on top of that revision.
7. **Given** a successful run, **When** it finishes, **Then** the operator's checked-out branch,
   working tree and current position in history are exactly as they were before the run started.

---

### User Story 3 - Surviving the death of the executing process (Priority: P1)

A run is in progress on attempt 3 of 5 when the process executing it is killed. A replacement process
starts. The run continues from attempt 3 — the earlier attempts are not re-proposed, not re-applied
and not re-validated, and the expensive external calls they made are not paid for twice.

**Why this priority**: this is the feature's differentiating guarantee and the demo the whole POC is
built to show. It is P1 because a loop that restarts from zero on a crash fails the project's central
claim, regardless of how well it fixes code.

**Independent Test**: start a run against the seed repository, wait until it is provably past its
second attempt, kill the executing process, start a fresh one, and confirm from the run's recorded
history that execution resumes at the attempt it was on, with zero additional fix-proposal calls
attributable to attempts that had already completed.

**Acceptance Scenarios**:

1. **Given** a run in progress on attempt N, **When** the executing process is killed and a
   replacement starts, **Then** the run resumes at attempt N.
2. **Given** a resumed run, **When** it completes, **Then** the number of fix-proposal calls recorded
   across the whole run equals the number of attempts made, with no duplicates for attempts that had
   already finished.
3. **Given** a completed run's recorded history, **When** it is replayed against the current
   orchestration code, **Then** replay succeeds without a determinism error.
4. **Given** a transient failure of an external dependency during an attempt, **When** the failure
   occurs, **Then** the affected step is retried automatically with backoff and the run continues
   rather than failing.

---

### User Story 4 - Inspecting what a run did (Priority: P2)

An operator needs to see, after the fact, what happened in a run: how many attempts were made, what
each proposed, what the validation verdict was each time, and how the run ended. The recorded
execution history is the source of truth for this.

**Why this priority**: required to *demonstrate* the guarantees in US1–US3 (the kill-resume evidence
is read from this history), but the loop itself works without any additional reporting surface. P2.

**Independent Test**: after any completed run, reconstruct the full attempt-by-attempt narrative from
the recorded history alone, without additional instrumentation.

**Acceptance Scenarios**:

1. **Given** a finished run, **When** its history is inspected, **Then** each attempt's proposal,
   application and validation verdict is identifiable, in order.
2. **Given** a run that ended in exhaustion, **When** its history is inspected, **Then** the terminal
   outcome and the reason are explicit.

---

### Edge Cases

- **A legitimately failing test**: a not-passed result, never an error. It becomes the feedback for
  the next proposal.
- **The execution environment itself broke** (runtime unavailable, setup failed, image missing): an
  error, so the step is retried.
- **The validation run hangs**: the execution time limit aborts it and it surfaces as an error; the
  run never blocks forever on a hung suite.
- **Re-applying a change after a retry**: always rebuilt from a clean checkout of the target
  revision, so the change never stacks.
- **A proposal that is not a valid change** (malformed, or does not apply): recorded as a failed
  attempt; the loop continues within its budget.
- **The fixer keeps proposing the same non-working change**: the attempt budget bounds the cost; the
  run ends in exhaustion.
- **The fixer weakens or deletes the failing test to force a green suite**: permitted (FR-018) and
  *not* detected automatically. The run still ends as fixed, so the winning patch is preserved in the
  run record and a human reading it is the only thing separating a real fix from a deleted test. This
  is an accepted trade-off of allowing unrestricted test edits, not an oversight.
- **The fixer edits code outside the failing test's blast radius**: permitted; the suite verdict is
  the only gate, so unrelated edits ride along in the winning patch and surface in the run record.
- **The external fixer or a dependency is transiently unavailable**: automatic retry with backoff.
- **The suite needs a dependency absent from the prebuilt environment image**: it cannot be fetched,
  because the environment has no network. This surfaces as an execution error (not a not-passed
  result) and is a seed-repository setup problem to fix before the run, not at run time.
- **The repository's tests are already green at the start**: the run terminates immediately as
  successful with zero attempts, rather than proposing a change to working code.
- **The process dies between applying a change and validating it**: on resume, application is redone
  from the clean base (safe, per the no-stacking rule) or, if already recorded as complete, skipped —
  either path converges to the same working tree.
- **The fix branch already exists carrying the same patch** (a retry, a resume, or a re-run of the
  same revision): creating it is a no-op and the run still ends as fixed. This is the idempotency
  guarantee, not an error.
- **The fix branch already exists carrying different content** (a previous run produced a different
  fix for the same revision): the run ends as conflicted, with the new patch preserved in the run
  record. Nothing is overwritten, so no earlier result is destroyed. Clearing the stale branch is a
  deliberate human act — and the reproducible demo script does exactly that before each run.
- **The process dies after creating the fix branch but before recording the outcome**: on resume the
  branch is found to exist carrying the winning patch, so creation is a no-op and the run completes
  normally.
- **The target repository is read-only or the branch cannot be written**: an execution error, so the
  step is retried; the validated patch is still preserved in the run record and no work is lost.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept a fix request identifying a target repository as a local
  filesystem path plus the exact revision to work from, and MUST derive the run's identity from that
  revision so the same revision cannot produce two concurrent runs. Runs are started manually by an
  operator; no automatic trigger is in scope.
- **FR-002**: The system MUST determine which tests fail by executing the suite itself before the
  first attempt — the operator is not required to name them — and MUST gather the context needed to
  propose a fix: those failing test(s), the associated source, and the observed failure output.
- **FR-003**: The system MUST validate every candidate change by executing the repository's test
  suite in a disposable, isolated environment that has no network access and no access to the host
  filesystem, the control plane's own working copy, or the control plane's credentials. Every
  dependency the suite needs MUST be present in the prebuilt environment image before the run starts.
- **FR-004**: The system MUST distinguish a failing test suite (a not-passed **result**) from a
  broken execution environment (an **error**), and MUST feed the former back into the loop while
  retrying the latter.
- **FR-005**: The system MUST enforce an explicit wall-clock limit on every validation run and MUST
  abort and report runs that exceed it.
- **FR-006**: The system MUST iterate propose → apply → validate until the suite is green or a
  configured maximum number of attempts is reached, and MUST stop at the first green verdict.
- **FR-007**: The system MUST include the previous attempt's failure output in the context used to
  produce the next proposal.
- **FR-008**: The system MUST express every candidate change as a patch (unified diff) against the
  target revision, and MUST apply it starting from a clean checkout of that revision, so that
  re-applying the same change yields an identical working tree.
- **FR-009**: The system MUST survive the death of the executing process: a replacement process MUST
  resume the run at the attempt it had reached, without re-executing any step that had already
  completed.
- **FR-010**: The system MUST NOT re-invoke the external fix proposer for an attempt whose proposal
  already completed, including after a resume.
- **FR-011**: The system MUST declare an explicit time limit for every step that calls an external
  dependency, and MUST report liveness for any step that can block on an external process so that a
  hung step is detected rather than waited on indefinitely.
- **FR-012**: The system MUST retry transient failures of external dependencies automatically with
  increasing backoff.
- **FR-013**: The system MUST record the run's full execution history — every attempt, its proposal,
  its application outcome and its validation verdict — as the authoritative account of what happened.
- **FR-014**: The system MUST terminate with an explicit outcome: fixed (with the validated change
  and the branch carrying it), or exhausted (with every attempt recorded).
- **FR-015**: The orchestration layer MUST be replayable: replaying a recorded history against the
  current orchestration code MUST succeed, and this MUST be enforced by an automated test.
- **FR-016**: The system MUST keep all credentials out of the orchestration layer and out of the
  isolated execution environment.
- **FR-017**: The system MUST terminate immediately as successful, with zero attempts, if the target
  repository's suite is already green at the start of the run — determined by the same initial
  validation run that discovers the failing tests.
- **FR-018**: The fixer MAY modify any file in the repository, including test files; no path is
  off-limits. Because of this, a green verdict alone does not establish that the original defect was
  fixed, so the system MUST preserve the full patch of the winning attempt in the run record for
  human inspection.
- **FR-019**: On a run that ends as fixed, the system MUST create, in the target repository, a branch
  whose name is derived deterministically from the target revision, pointing at a commit that applies
  the winning patch on top of that revision. The system MUST NOT change the operator's checked-out
  branch, working tree, or current position in history while doing so.
- **FR-020**: Branch creation MUST be idempotent. Before creating, the system MUST check whether the
  branch already exists: if it exists and already carries the winning patch, the step MUST succeed as
  a no-op; if it exists carrying anything else, the system MUST record an explicit conflict outcome
  and MUST NOT overwrite it. A retry or a resume MUST therefore never produce a second branch, a
  duplicated commit, or a lost previous result.

### Key Entities

- **Fix Request**: what the run is asked to do — target repository, exact revision, attempt budget.
  Its revision determines the run's identity.
- **Repository Context**: the material gathered for the fixer — the failing test(s) discovered by the
  initial validation run, the associated source, and the observed failure output.
- **Attempt**: one pass of propose → apply → validate. Carries its ordinal, the proposed change, the
  application outcome and the validation verdict.
- **Proposed Change**: a candidate modification expressed as a unified diff against the target
  revision, so it can be re-applied from a clean base. It may touch any file in the repository,
  including tests.
- **Validation Verdict**: the structured outcome of running the suite — passed or not-passed, plus
  the captured output. Distinct from an execution error.
- **Run Outcome**: the terminal state — fixed (with the validated change and the branch carrying it),
  exhausted (with the attempt record), or conflicted (a fix was validated but the target branch name
  is already taken by different content).
- **Fix Branch**: the deliverable of a successful run — a branch in the target repository whose name
  is a deterministic function of the target revision, pointing at a commit applying the winning patch.
  Its determinism is what makes creating it safe to retry, and what lets a later phase push it and
  open a pull request without re-deriving anything.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given the versioned seed repository with a deterministic failing test, an end-to-end
  run reaches a green verdict within the configured attempt budget, reproducibly, from a single
  documented command.
- **SC-002**: 100% of runs interrupted by killing the executing process resume and complete, without
  re-executing any step that had already completed.
- **SC-003**: Zero fix-proposal calls are made for attempts that had already completed before an
  interruption — counted from the run's recorded history.
- **SC-004**: 100% of validation runs execute in a disposable isolated environment with no network
  access; zero files outside that environment are modified by tested code, and zero outbound
  connections succeed from inside it — both verified by an automated check.
- **SC-005**: A failing test never surfaces as an execution error, and a broken execution environment
  never surfaces as a not-passed result — both directions covered by automated tests.
- **SC-006**: Re-applying the same proposed change twice produces an identical working tree, verified
  by an automated test.
- **SC-007**: No run exceeds its attempt budget, and no step waits indefinitely — every step has a
  declared time limit.
- **SC-008**: Replaying a recorded run history against the current orchestration code succeeds, as an
  automated test in the suite.
- **SC-009**: 100% of runs that end as fixed carry the winning attempt's complete patch in the run
  record, so a reviewer can tell a genuine fix from a weakened test — the required mitigation for the
  unrestricted test edits allowed by FR-018.
- **SC-010**: Executing branch creation twice for the same run produces exactly one branch at exactly
  one commit, verified by an automated test — and a run interrupted after branch creation completes
  without producing a second.
- **SC-011**: Zero successful runs alter the operator's checked-out branch, working tree or current
  position in history, verified by comparing repository state before and after in an automated test.

## Assumptions

- The seed repository is Python with a deterministic failing test, small enough that its suite runs
  in well under the validation time limit. Multi-language support is out of scope for this feature.
- The failure is a single-file, logic-level defect — the class of bug a fixer can address from the
  failing test plus the relevant source. Failures requiring dependency changes, migrations or
  multi-service coordination are out of scope.
- The attempt budget is a configuration value with a small default (order of 3–5); it is not
  discovered dynamically.
- The repository is handed over as a local filesystem path already containing the target revision;
  cloning from a remote, and any credential needed to do so, is out of scope for this feature.
- The execution environment runtime is installed and available on the host that executes validation,
  and a prebuilt environment image carrying every dependency the seed suite needs is built ahead of
  the run — no dependency resolution happens during a run.
- A durable execution engine is available locally for development runs; operating it (deployment,
  scaling, hosting) is out of scope.
- No trigger surface is in scope: runs are started manually by an operator. The webhook trigger, the
  human approval gate and pull request creation are later phases and are explicitly out of scope
  here.
- The local repository handed to the run is writable, so the fix branch can be created in it. Pushing
  that branch to a remote and opening a pull request from it belong to a later phase.
- The intended branch naming convention is `fix/<revision>`. FR-019 requires only that the name be a
  deterministic function of the revision; the exact template is pinned during planning, and a later
  phase depends on it being stable rather than on this particular spelling.
- No personal data is processed.
