# Quickstart: CI Trigger and Pull Request

**Feature**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22

One thing to prove: a build-failure notification, with nobody watching, becomes a pull request —
and sending it ten times still produces one.

**Everything here runs offline.** No network, no token, no CI installation, no hosted repository.

## Prerequisites

Same as feature 001 — Python 3.12, Docker, the Temporal CLI — plus nothing. The repository host is a
local process and the remote is a bare repository on disk.

```bash
uv pip install -e ".[dev]"
docker build -t fixloop-sandbox:latest sandbox/
```

## Run it

Four terminals.

```bash
# 1 — durable execution engine
temporal server start-dev

# 2 — the stand-in for the repository host
python scripts/fake_host.py

# 3 — worker and ingress
python -m control_plane.worker &
python -m control_plane.ingress

# 4 — send a failed build
./scripts/demo_webhook.sh
```

`demo_webhook.sh` materializes the seed repository, creates a bare clone of it to act as the remote,
signs a payload with the shared secret, posts it, waits for the run, and prints the pull request.

**Expected**:

```text
==> posting a signed build-failure notification
    HTTP 202  started  workflow fix-4b1a0d431c4e

==> waiting for the run
    status fixed, 3 attempts

==> the pull request
    #1  Fix failing tests at 4b1a0d431c4e
    created by this run: yes
```

## The property that matters (SC-002, SC-004)

```bash
./scripts/demo_webhook.sh --duplicate 10
```

Sends the same notification ten times, concurrently.

```text
    1 x HTTP 202  started
    9 x HTTP 200  deduplicated
==> runs for this commit: 1
==> pull requests for this branch: 1
```

Nine of them are `200`, not an error: the sender did nothing wrong and must stop retrying. **There
is no deduplication code in this repository** — `workflow_id = fix-<sha>` plus
`WorkflowIDReusePolicy.REJECT_DUPLICATE` makes the engine refuse the second start, and the ingress
turns that refusal into an answer.

## Verification checklist

| Criterion | How to check |
|---|---|
| SC-001 notification → run | `./scripts/demo_webhook.sh` ends with a pull request |
| SC-002 ten deliveries, one run | `./scripts/demo_webhook.sh --duplicate 10` |
| SC-003 bad input starts nothing | `pytest tests/integration/test_ingress.py` |
| SC-003a completed commit is refused | `pytest tests/integration/test_ingress_dedup.py` |
| SC-004 one pull request on retry | `pytest tests/integration/test_open_pr.py` |
| SC-005 no PR when exhausted/conflicted | `pytest tests/integration/test_open_pr.py` |
| SC-006 nothing is merged | `pytest tests/unit/test_host_adapter.py` — no merge call exists |
| SC-007 ingress does not block | `pytest tests/integration/test_ingress.py` measures the response |
| SC-008 every outcome recorded | `pytest tests/unit/test_ingress_outcomes.py` |
| SC-010 works with no network | `pytest` on a disconnected machine |

```bash
pytest                       # the whole suite, offline
ruff check src tests && mypy
```

## What this does not prove

**The real repository host adapter is never exercised.** A pull request is an API concept, not a git
one, so an offline run proves this side of the contract — one request, idempotent, with the right
contents — and not that GitHub accepts it. The stand-in implements
[contracts/host_api.md](./contracts/host_api.md), so a divergence between the two is a documentation
bug rather than a surprise, but it is a real limit and it is the same position the fixer's real
adapter is in.

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `401` from the ingress | Payload signed with a different secret | Both sides read `CONTROL_PLANE_WEBHOOK_SECRET` |
| `503` from the ingress | Temporal is not running | `temporal server start-dev` |
| `200 deduplicated` on a first run | A run for that sha already exists from an earlier demo | Terminate it, or use a fresh seed revision |
| Run ends `conflicted` | `fix/<sha>` left over from a previous run | The demo script clears it; if run by hand, delete it |
| Pull request never appears | The host stand-in is not running | Terminal 2 |
