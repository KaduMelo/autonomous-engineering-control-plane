# Phase 1 Data Model: CI Trigger and Pull Request

**Feature**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22

Same two rules as feature 001: everything here crosses a boundary, so it must serialize, and none of
it may carry a live handle. The clone path is deliberately absent — it is derived from
`(source, revision)` wherever it is needed, never passed.

## New entities

### BuildNotification — what the sender posts

| Field | Type | Notes |
|---|---|---|
| `repository_url` | `str` | Clone source. May be a URL or a local path (FR-003a) |
| `revision` | `str` | Full commit sha. Determines the run's identity |
| `conclusion` | `"failure" \| "success" \| str` | Only `failure` starts a run (FR-003) |
| `repository_full_name` | `str` | `owner/name`, used to address the host's API |

Parsed **after** its signature verifies, never before.

### IngressOutcome — what the ingress decided

| Field | Type | Notes |
|---|---|---|
| `decision` | `"started" \| "deduplicated" \| "rejected" \| "failed"` | |
| `revision` | `str \| None` | Absent when the payload could not be parsed |
| `reason` | `str \| None` | Required when `rejected` or `failed` (FR-015) |
| `workflow_id` | `str \| None` | Set when `started` or `deduplicated` |
| `previous_status` | `str \| None` | For `deduplicated`: how the earlier run ended, if it has |

### PullRequestRef — the published result

| Field | Type | Notes |
|---|---|---|
| `number` | `int` | |
| `url` | `str` | |
| `created` | `bool` | `False` when this run found one that already existed (FR-010) |

### Activity inputs

```text
CloneInput        source, revision
OpenPullRequestInput   source, repository_full_name, revision, branch, outcome_summary
```

`OpenPullRequestInput` carries a summary rather than the whole `RunOutcome`: the body needs the
failing tests, the winning diff and the attempt count (FR-011), and sending an unbounded run record
through the payload would push against the history's size limits for no benefit.

## Extended entities

`RunOutcome` gains `pull_request: PullRequestRef | None`, set only when `status == "fixed"`.
`FixRequest` gains nothing — its `repo_path` already accepts a URL or a path, which is exactly what
FR-003a means by one mechanism.

## Ingress state machine

```text
                POST /webhook
                     |
              verify signature ──fail──► 401  rejected(bad signature)
                     |
                  parse body ──fail──► 400  rejected(malformed)
                     |
             conclusion == failure? ──no──► 200  rejected(not a failure)
                     |
            start workflow  id=fix-<sha>
            reuse policy: REJECT_DUPLICATE
                /            |            \
      AlreadyStarted     started      engine unreachable
            |                |                 |
     200 deduplicated   202 started        503 failed
     (+ previous status)                  (sender retries)
```

Two status codes carry the design's opinions. A duplicate is **200**, not a conflict: the sender did
nothing wrong and must stop retrying. An engine that is down is **503**, never an acknowledgement —
accepting a build the system did not start a run for is the one failure that loses work silently.

## Serialization rules

Unchanged from feature 001, plus:

1. **The raw body is kept as bytes** until the signature verifies. Decoding, normalizing or
   re-serializing it before verification would verify something the sender did not sign.
2. **The clone path is never a field.** It is derived from `(source, revision)` at the point of use.
3. **The pull request body is rendered in the activity**, not in the workflow — it reads a run
   summary and produces text, which is I/O-shaped work and must not live in deterministic code.
