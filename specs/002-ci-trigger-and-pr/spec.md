# Feature Specification: CI Trigger and Pull Request

**Feature Branch**: `002-ci-trigger-and-pr`

**Created**: 2026-09-22

**Status**: Draft

**Input**: PRD Phase 2 — "Trigger + PR (closes C)": ingress webhook (start-per-build, dedup) plus an idempotent `open_pr`. Completion milestone: **failed build → green pull request**. This phase closes the declared scope.

## Clarifications

### Session 2026-09-22

- Q: With a webhook trigger there is no local path — how does the control plane obtain the
  repository? → A: The notification carries a **repository URL plus a commit sha**, and the run
  clones from it into a disposable workspace. This does not revoke feature 001's local-path
  decision: a clone source may be a URL *or* a local path, so manual runs keep working through the
  same single mechanism rather than a second code path.

### Defaults taken without asking

Recorded here so they are visible and correctable, rather than buried in the plan.

- **A notification for a commit whose run already finished is refused**, and the ingress reports the
  earlier outcome. One broken commit has one answer. Starting a second run would re-bill the model
  for work already done and, when the first run ended `fixed`, would collide with the existing
  branch and end `conflicted` anyway. This resolves the item the quality checklist left unchecked.
- **Authenticity is verified with an HMAC signature over the request body**, compared in constant
  time — what GitHub itself sends. Stateless, no external dependency, and the same verification
  serves both a real delivery and a locally generated one.
- **Tests and the default demo run fully offline**: a locally generated signed payload against a
  bare repository acting as the remote. The demo script points at a real GitHub repository when one
  is configured. Offline by default keeps the demo reproducible, which the constitution requires;
  the real path is what proves the milestone end to end.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A failed build starts a fix without anyone asking (Priority: P1)

A build fails in CI. The CI platform notifies the control plane, which starts a fix run for that
exact commit. Nobody opens a terminal, and nobody has to notice the failure first.

**Why this priority**: this is the half of the milestone that removes the human from the front of
the loop. Feature 001 already turns a red repository green; without this, someone still has to
notice and type a command.

**Independent Test**: send the control plane a notification describing a failed build for a known
commit and observe that a fix run for that commit starts, with no operator action.

**Acceptance Scenarios**:

1. **Given** a notification for a failed build carrying a commit identifier, **When** the ingress
   receives it, **Then** a fix run for that commit starts.
2. **Given** a notification for a build that **succeeded**, **When** the ingress receives it,
   **Then** no run starts.
3. **Given** a notification that is malformed or missing the commit identifier, **When** the ingress
   receives it, **Then** it is rejected with a client error and no run starts.
4. **Given** a notification whose authenticity cannot be verified, **When** the ingress receives it,
   **Then** it is rejected and no run starts.
5. **Given** the durable execution engine is unreachable, **When** a valid notification arrives,
   **Then** the ingress reports a server error so the sender retries, rather than silently dropping
   the build.

---

### User Story 2 - The same build never starts two runs (Priority: P1)

CI platforms retry deliveries, re-run jobs, and report several failing checks for one commit. All of
those describe the same broken commit, and the control plane must spend one fix run on it, not four.

**Why this priority**: duplicate runs multiply the cost of the most expensive part of the system for
no benefit, and two runs racing on the same commit would fight over the same branch name. P1
alongside US1 because a trigger without deduplication is worse than no trigger.

**Independent Test**: send the same notification several times, concurrently and in sequence, and
observe exactly one run for that commit.

**Acceptance Scenarios**:

1. **Given** a run is already in progress for a commit, **When** another notification for the same
   commit arrives, **Then** no second run starts and the ingress reports success rather than an
   error — the sender did nothing wrong.
2. **Given** several notifications for the same commit arrive at the same moment, **When** they are
   processed, **Then** exactly one run exists for that commit.
3. **Given** notifications for two different commits, **When** they arrive, **Then** two runs exist.
4. **Given** a run for a commit has already finished, **When** a new notification for that same
   commit arrives, **Then** no second run starts and the ingress reports the earlier outcome.

---

### User Story 3 - The validated fix becomes exactly one pull request (Priority: P1)

A run reaches a green verdict and writes its fix branch. That branch is published and a pull request
is opened against the repository's default branch, describing what was fixed and what evidence
supports it. A retry of that step never produces a second pull request.

**Why this priority**: this is the other half of the milestone. A fix that stays on a local branch
is not a delivered fix, and a control plane that opens duplicate pull requests on retry is one
nobody will point at a real repository.

**Independent Test**: complete a run against a repository with a remote, confirm exactly one pull
request exists; execute the publishing step a second time and confirm there is still exactly one.

**Acceptance Scenarios**:

1. **Given** a run that ended with a validated fix branch, **When** the run finishes, **Then** the
   branch is published to the remote and exactly one pull request is open for it.
2. **Given** the publishing step is retried after a transient failure, **When** it runs again,
   **Then** there is still exactly one pull request.
3. **Given** a pull request already exists for the fix branch, **When** the step runs, **Then** it
   succeeds as a no-op and reports the existing pull request.
4. **Given** the run ended as exhausted or conflicted, **When** it finishes, **Then** no pull
   request is opened.
5. **Given** a pull request is opened, **When** a reviewer reads it, **Then** it states the commit
   that broke, the failing tests, the change that fixed them, and how many attempts it took.

---

### User Story 4 - Knowing what the ingress did (Priority: P2)

An operator needs to see whether a notification was accepted, rejected, or deduplicated, and why,
without reading the durable engine's history to find out that nothing happened.

**Why this priority**: the ingress is the one component whose failures are invisible in the run
history — a rejected notification produces no run at all. P2 because the system works without it,
but a silent trigger is hard to trust.

**Independent Test**: send accepted, rejected and duplicate notifications, and confirm each outcome
is distinguishable from the ingress's own output.

**Acceptance Scenarios**:

1. **Given** any notification, **When** the ingress handles it, **Then** its outcome — started,
   deduplicated, rejected, or failed — is recorded with the commit it referred to.
2. **Given** a rejected notification, **When** its outcome is inspected, **Then** the reason is
   explicit.

---

### Edge Cases

- **The same commit is reported by several failing checks**: one run. Deduplication is by commit,
  not by notification.
- **The sender retries a delivery it already made**: no second run, and the ingress reports success
  so the sender stops retrying.
- **A notification arrives for a commit that no longer exists** (force-pushed, branch deleted): the
  run starts and fails at the clone or checkout step, with a clear reason. The ingress does not try
  to validate repository state before starting.
- **The clone fails** (network, permissions, unknown host): an infrastructure error, retried. It is
  never mistaken for a repository whose tests fail.
- **A notification arrives while the engine is down**: the ingress reports a server error so the
  sender retries. It never acknowledges a build it did not start a run for.
- **The remote rejects the branch push** (permissions, protected refs): an error, retried; the
  validated fix is still preserved in the run record.
- **The pull request step runs twice**: deterministic branch plus check-if-exists means one pull
  request, and the second call reports the first.
- **A pull request for the fix branch was closed by a human**: treated as a deliberate act. The
  behavior is explicit and does not silently reopen it.
- **The run ends as conflicted** (the fix branch already carried different content): no push, no
  pull request. Nothing is overwritten.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose an ingress endpoint that accepts build-failure notifications
  from a CI platform.
- **FR-002**: The ingress MUST verify the authenticity of each notification before acting on it, and
  MUST reject unverifiable ones without starting a run.
- **FR-003**: The ingress MUST start a fix run only for notifications that describe a **failed**
  build carrying a commit sha and a repository URL.
- **FR-003a**: The system MUST obtain the repository by cloning the given source into a disposable
  workspace at the start of a run. A clone source MAY be a URL or a local path, so manually started
  runs and webhook-started runs share one mechanism.
- **FR-004**: The ingress MUST derive the run's identity from the commit, so that the durable
  execution engine itself rejects a second concurrent run for that commit.
- **FR-005**: A duplicate notification MUST be reported to the sender as success, not as an error —
  it describes a build the system is already handling.
- **FR-005a**: A notification for a commit whose run has already finished MUST NOT start a second
  run. The ingress MUST report the earlier outcome instead.
- **FR-006**: The ingress MUST respond to the sender promptly, without waiting for the fix run to
  finish.
- **FR-007**: The ingress MUST report a server error, rather than acknowledging, when it cannot start
  a run for a valid notification, so that the sender retries.
- **FR-008**: The system MUST publish the validated fix branch to the repository's remote when a run
  ends with a fix.
- **FR-009**: The system MUST open exactly one pull request for a published fix branch, targeting the
  repository's default branch.
- **FR-010**: Opening the pull request MUST be idempotent: before creating, the system MUST check
  whether one already exists for that branch, and if so succeed as a no-op reporting the existing
  one.
- **FR-011**: The pull request description MUST state the commit that broke, the failing tests, the
  change that fixed them, and the number of attempts taken.
- **FR-012**: The system MUST NOT open a pull request for a run that ended as exhausted or
  conflicted.
- **FR-013**: The system MUST NOT merge a pull request. Merging remains a human act.
- **FR-014**: Credentials for the CI platform and the repository host MUST stay out of workflow code
  and out of the validation sandbox, with minimum scope.
- **FR-015**: The ingress MUST record the outcome of every notification — started, deduplicated,
  rejected, or failed — together with the commit it referred to.
- **FR-016**: Publishing and pull request creation MUST declare explicit time limits and MUST retry
  transient failures of the repository host.

### Key Entities

- **Build Notification**: what the CI platform sends — repository identifier, commit identifier,
  build conclusion, and whatever proves the notification is authentic.
- **Ingress Outcome**: what the ingress decided — started, deduplicated, rejected, or failed — with
  the commit and, for a rejection, the reason.
- **Pull Request Reference**: the published result — its number, its URL, and whether this run
  created it or found it already there.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A failed build reported by CI results in a fix run for that commit with zero human
  actions, demonstrated end to end from a reproducible script.
- **SC-002**: Sending the same notification ten times, concurrently, produces exactly one run.
- **SC-003**: 100% of notifications that are unverifiable, malformed, or describe a successful build
  result in no run.
- **SC-003a**: A notification for an already-completed commit starts zero new runs and bills the
  model zero times, verified by an automated test.
- **SC-004**: Executing the publish-and-open step twice for the same fix produces exactly one pull
  request, verified by an automated test.
- **SC-005**: Zero pull requests are opened for runs that ended as exhausted or conflicted.
- **SC-006**: Zero pull requests are merged by the system.
- **SC-007**: The ingress responds to a notification in well under the sender's delivery timeout,
  measured, and never waits for the fix run.
- **SC-008**: Every notification has a recorded outcome; an operator can tell a rejection from a
  deduplication without reading the run history.
- **SC-009**: A reviewer can tell from the pull request alone what broke, what changed, and how many
  attempts it took, without opening the durable engine's UI.

## Assumptions

- The target repository is hosted on GitHub and the control plane has a token with permission to
  clone it, push a branch, and open a pull request on it. Other hosts are out of scope.
- The CI platform can send an authenticated HTTP notification on build completion. The seed setup
  stands in for a real CI installation.
- Feature 001 is merged: the durable loop, the sandbox, and the deterministic `fix/<sha>` branch
  already exist. This feature adds the entry point and the exit, not the loop.
- **The human approval gate is not in this feature.** It is PRD Phase 1, which is being delivered
  after this one. Until then a validated fix goes straight to a pull request. This does not conflict
  with the constitution's ban on "autonomous merge without a human gate": a pull request is a request
  for review, and FR-013 forbids merging it.
- The repository whose build failed and the repository the pull request targets are the same.
- The control plane runs where the CI platform can reach it. Exposing it — tunnels, ingress,
  deployment — is out of scope.
- No personal data is processed beyond what a commit already carries.
