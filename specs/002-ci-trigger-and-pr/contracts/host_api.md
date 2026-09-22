# Repository Host API — the subset this feature uses

**Feature**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22

This file is the contract the real adapter and the local stand-in both implement. It exists because
only the stand-in is ever exercised: without a written contract, a divergence between the two would
be discovered the first time someone points the system at a real repository. With one, a divergence
is a documentation bug.

Two endpoints. Shapes follow GitHub's.

---

## `GET /repos/{owner}/{repo}/pulls`

Find an existing pull request for a branch.

| Query parameter | Value |
|---|---|
| `head` | `{owner}:{branch}` |
| `state` | `open` |

**200** → a JSON array, empty or containing objects with at least:

```json
[{ "number": 42, "html_url": "https://host/owner/repo/pull/42", "head": { "ref": "fix/<sha>" } }]
```

---

## `POST /repos/{owner}/{repo}/pulls`

Create a pull request.

```json
{ "title": "...", "head": "fix/<sha>", "base": "main", "body": "..." }
```

| Status | Meaning | Contract |
|---|---|---|
| `201` | Created | result, `created=True` |
| `422` | Already exists for this head | **result**, `created=False` — not an error |
| `401` `403` `404` | Bad credential, no permission, unknown repository | non-retryable error |
| `5xx`, `429` | Host trouble | retryable error |

The `422`-is-success rule is the one a reader should not skim: GitHub returns it both for "a pull
request already exists" and for genuine validation problems, so the adapter matches on the message
and treats only the former as success. Anything else in a `422` is a non-retryable error.

---

## Authentication

`Authorization: Bearer <token>`, read inside the activity. Minimum scope: push a branch and open a
pull request on one repository. Never merge — no scope for it is requested, so FR-013 is enforced by
the credential and not only by the code.

---

## The stand-in

`scripts/fake_host.py` implements both endpoints over in-memory state and is a runnable process, so
the demo exercises a real HTTP round trip rather than a function call. Its base URL is what the
adapter is pointed at; nothing else about the adapter changes between offline and real.
