# Activity Contracts (new in 002)

**Feature**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22

Two new activities. Feature 001's five are unchanged; the workflow gains one step at the end.

Shared retry policy as in 001: 1s initial, ×2, 60s cap, 3 attempts, with
`non_retryable_error_types` extended.

---

## `ensure_clone(source: str, revision: str) -> str`

**Promises**: A local clone of `source` containing `revision` exists, and returns the *cache key*
that identifies it — not a path (see below).

**Mechanism**: The directory is a deterministic function of `source`. Miss → clone; hit → fetch.
Then verify `revision` resolves.

**Raises**:
- `CloneError` for network, auth or unknown-host failures — retryable.
- `ApplicationError(non_retryable=True)` when `revision` does not exist after a successful
  clone-and-fetch. A force-push will not undo itself.

**Ceilings**: `start_to_close_timeout=300s`, `heartbeat_timeout=30s` — a clone of an unexpectedly
large repository must be detected, not waited on.

**Idempotent because**: it is a pure function of `(source, revision)`. Calling it twice converges;
deleting the cache directory and calling it again converges to the same place.

**Why it returns a key and not a path**: other activities re-derive the directory from the same
inputs. Returning a path would put filesystem state in the payload, which is what makes a resume on
another worker fail — the lesson feature 001's design note records.

---

## `open_pr(request: OpenPullRequestInput) -> PullRequestRef`

**Promises**: The fix branch is published and exactly one pull request exists for it, targeting the
repository's default branch.

**Sequence**: push `fix/<revision>` → query the host for an open pull request with that head →
create only if absent.

| Host response | Contract |
|---|---|
| Query returns a matching open pull request | **result** — `created=False`, no create call |
| Create succeeds | **result** — `created=True` |
| Create rejected with "a pull request already exists for this branch" | **result** — `created=False`; another attempt won the race |
| 5xx, timeout, rate limit | **error** — retried with backoff |
| 401 / 403 / 404 | **non-retryable error** — a bad token or a missing repository will not fix itself |
| Push rejected (protected ref, permissions) | **error** — retried; the validated fix stays in the run record |

**Ceilings**: `start_to_close_timeout=120s`.

**Idempotent because**: the branch name is deterministic (feature 001, FR-019) and creation is
guarded twice — check-before-create, and treat the host's conflict as success. The second guard is
what makes the property hold under interleaved retries rather than merely repeated ones.

**Credential boundary**: the host token is read here, inside the activity. It never enters workflow
code and never enters the sandbox (FR-014).

**Body contents** (FR-011): the commit that broke, the failing tests, the winning diff, and the
attempt count. Rendered in the activity — turning a run summary into text is I/O-shaped work and
must not live in deterministic code.

---

## What changes in the workflow

One step, after `create_fix_branch` and only on `status == "fixed"`:

```text
create_fix_branch ──► open_pr ──► RunOutcome(status="fixed", branch=..., pull_request=...)
```

`exhausted` and `conflicted` return as before, with no pull request (FR-012). The workflow does not
merge and has no path that could (FR-013).

The recorded history for the replay test must be re-captured, because the activity sequence changed
on purpose — which is exactly when re-recording is supposed to happen.
