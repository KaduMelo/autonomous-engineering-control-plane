# Phase 0 Research: CI Trigger and Pull Request

**Feature**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22

Guarantee → Mechanism → Evidence → Trade-off, per Principle VII.

---

## D1 — Deduplication is the engine's job, not ours

**Guarantee**: One broken commit produces exactly one run, whether the duplicate arrives during the
first run or long after it finished (FR-004, FR-005a, SC-002).

**Mechanism**: `workflow_id = f"fix-{sha}"` — already how feature 001 starts runs — plus
`WorkflowIDReusePolicy.REJECT_DUPLICATE` on the start call. Temporal then refuses a second start for
that id in **any** state, running or closed, and raises `WorkflowAlreadyStartedError`. The ingress
catches it, fetches the existing run's outcome, and answers `200` with that outcome.

**Evidence**: The two dedup requirements collapse into one setting. There is no dedup table to keep
consistent, no cursor to advance, no lock to hold, and nothing to get wrong when two deliveries race
— the engine serializes them because they compete for the same workflow id.

**Trade-off**: A commit can never be retried, even when the first run ended `exhausted` and the
budget has since been raised. Re-running one then requires terminating or deleting the prior run by
hand. Accepted deliberately: the alternative (`ALLOW_DUPLICATE_FAILED_ONLY`, which permits a new run
after a failed one) makes "did this commit already get an answer" a question with a conditional
answer, and a conditional dedup rule is how duplicate-spend bugs get in.

---

## D2 — The notification is untrusted until its signature verifies

**Guarantee**: Nothing acts on a notification the sender cannot prove it wrote (FR-002, SC-003).

**Mechanism**: HMAC-SHA256 over the **raw request body**, using a shared secret, compared with
`hmac.compare_digest`. This is what GitHub sends as `X-Hub-Signature-256`, so the same code path
serves a real delivery and the locally generated one the tests use. The body is read as bytes and
verified **before** it is parsed as JSON — parsing is already acting on it.

**Evidence**: Three failure modes are closed at once: a forged notification (no valid signature), a
tampered one (signature covers the body), and a replayed one from a different secret. Constant-time
comparison closes the timing side channel that `==` leaves open.

**Trade-off**: A shared secret is symmetric — anyone who can verify can also forge. For a POC with a
single sender that is the right size; mTLS or asymmetric signatures would bring certificate
management the negative scope does not cover.

---

## D3 — The repository arrives as a URL, and the clone is content-addressed

**Guarantee**: A run can obtain the repository from a URL, while activities stay pure functions of
their inputs (FR-003a, Principles I and III).

**Mechanism**: `ensure_clone(source, revision) -> Path` computes a deterministic directory from a
hash of the source, clones there on a miss and fetches on a hit, then verifies the revision exists.
Any activity can call it and get the same result, so nothing has to be handed between activities and
nothing has to survive a worker dying. Feature 001's `materialize`, `apply_diff` and `tree_hash` then
operate on that local clone unchanged.

**Evidence**: The clone is derivable from the activity's own inputs, which is the test Principle I
actually applies — unlike a path produced by one activity and consumed by another, a cache miss here
is recoverable by re-deriving it.

**Trade-off**: It is a cache, so it is disk that grows and can be stale in principle. Mitigated by
keying on the source and fetching before use, and by the fact that a corrupted entry is fixed by
deleting it. The stateless alternative — cloning inside each of the four activities that read the
repository — is simpler but means four clones per attempt against a remote.

---

## D4 — The pull request is idempotent by asking first

**Guarantee**: Exactly one pull request per fix branch, no matter how often the step runs
(FR-009, FR-010, SC-004).

**Mechanism**: Three steps, in order: push the deterministic branch `fix/<sha>` (already idempotent
from feature 001 — the same ref, the same commit); query the host for an open pull request whose
head is that branch; create one only if none exists. The host's own "a pull request already exists
for this branch" rejection is additionally treated as success, because between the query and the
create another attempt may have won the race.

**Evidence**: Two independent guards — check-before-create and treat-conflict-as-success — which is
what makes the property hold under a retry that interleaves rather than merely repeats.

**Trade-off**: An extra API call on every run. Negligible, and it is what turns a race from a
duplicate pull request into a no-op.

---

## D5 — The repository host sits behind a port, and only the stand-in is exercised

**Guarantee**: The whole feature runs with no network, no token and no hosted repository (SC-010),
without the production path becoming fictional.

**Mechanism**: `RepositoryHostPort` with two adapters. `GitHubHost` speaks the real API over
`httpx2` with a configurable base URL. `scripts/fake_host.py` is a runnable Starlette app
implementing the two endpoints this feature uses, storing pull requests in memory. Tests drive the
stand-in in-process via an ASGI transport; the demo runs it as a process so the same HTTP path is
exercised.

**Evidence**: `httpx2` is already installed as a dependency of `anthropic`, so this adds no package —
it only declares what is there. Choosing `httpx` instead would install a second, parallel HTTP stack.

**Trade-off**: **The real adapter is never exercised.** An offline run proves the control plane's
side of the contract — one request, idempotent, with the right contents — and not that GitHub accepts
it. This is the same position `AnthropicFixer` is in, and it is stated in the spec rather than left
for a reviewer to discover. What contains the risk is that the stand-in implements the contract
documented in `contracts/host_api.md`, so a divergence is a documentation bug rather than a surprise.

---

## D6 — The ingress is a separate process that holds nothing

**Guarantee**: The ingress cannot become a second source of truth, and it answers fast (FR-006,
SC-007).

**Mechanism**: A Starlette app with one route, started as `python -m control_plane.ingress`. It
verifies, parses, starts the workflow, and returns. It holds no queue, no retry state and no
database. When it cannot start a run it returns `503` so the **sender** retries — the sender already
has a durable retry queue, and duplicating it here would mean two of them disagreeing.

**Evidence**: Every state the system has lives in the engine. Killing the ingress loses nothing; the
sender redelivers.

**Trade-off**: A notification that arrives while the engine is down is not accepted. That is the
intent: acknowledging a build the system did not start a run for is the one failure that loses work
silently.

---

## D7 — What the new failures mean

**Guarantee**: The result-vs-exception contract survives contact with the network
(Principle V, NON-NEGOTIABLE).

**Mechanism**:

| Situation | Contract |
|---|---|
| Clone fails (network, auth, unknown host) | **error** — retried |
| Revision missing after a successful clone | **non-retryable error** — a force-push will not undo itself |
| Push rejected (protected branch, permissions) | **error** — retried; the validated fix stays in the run record |
| Host returns 5xx or a rate limit | **error** — retried with backoff |
| Host returns "pull request already exists" | **result** — no-op success |
| Host returns 401/403/404 | **non-retryable error** — a bad token or a missing repo will not fix itself |

**Evidence**: The two cases that most look like each other are separated explicitly: a pull request
that already exists is success, while a host that rejects the credential is a hard stop rather than
three doomed retries.

**Trade-off**: The table is GitHub-shaped. A different host needs its own mapping — the same honest
limitation the pytest exit-code table has in feature 001.
