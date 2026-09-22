# Ingress Contract

**Feature**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22

One route. It verifies, decides, starts, and returns — it never waits for the run.

## `POST /webhook`

### Request

| Header | Required | Meaning |
|---|---|---|
| `X-Hub-Signature-256` | yes | `sha256=<hex>`, HMAC-SHA256 of the **raw body** with the shared secret |
| `Content-Type` | yes | `application/json` |

Body:

```json
{
  "repository_url": "https://github.com/owner/name.git",
  "repository_full_name": "owner/name",
  "revision": "4b1a0d431c4eda26a485496e3f50504dd49d7f2f",
  "conclusion": "failure"
}
```

### Responses

| Status | Decision | When | Body |
|---|---|---|---|
| `202` | `started` | A run was started for this commit | `{decision, revision, workflow_id}` |
| `200` | `deduplicated` | A run for this commit already exists, running or finished | `{decision, revision, workflow_id, previous_status}` |
| `200` | `rejected` | Valid notification, but the build did not fail | `{decision, reason}` |
| `400` | `rejected` | Body is not JSON, or a required field is missing | `{decision, reason}` |
| `401` | `rejected` | Signature missing or invalid | `{decision, reason}` |
| `503` | `failed` | The durable execution engine could not be reached | `{decision, reason}` |

### Rules

- **Verify before parsing.** The body is read as bytes and its signature checked before any attempt
  to interpret it. Parsing is already acting on untrusted input.
- **Constant-time comparison.** `hmac.compare_digest`, never `==`.
- **A duplicate is not an error.** `200`, so the sender stops retrying. It did nothing wrong.
- **Never acknowledge what was not started.** If the engine is unreachable the answer is `503`;
  the sender's own retry queue is the durable one, and duplicating it here would mean two queues
  that can disagree.
- **Never block on the run.** The response is written as soon as the workflow is started.
- **Every request produces a recorded outcome** (FR-015), including the rejections that leave no
  trace anywhere else.
