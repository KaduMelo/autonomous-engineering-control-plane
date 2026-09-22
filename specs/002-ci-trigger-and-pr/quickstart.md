# Quickstart: CI Trigger and Pull Request

**Feature**: `002-ci-trigger-and-pr` | **Date**: 2026-09-22

One thing to prove: a build-failure notification, with nobody watching, becomes a pull request —
and sending it ten times still produces one.

**Everything here runs offline.** No network, no token, no CI installation, no hosted repository.

## Prerequisites

Same as feature 001 — Python 3.12, Docker, the Temporal CLI — plus nothing. The repository host is a
local process and the remote is a bare repository on disk.

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
docker build -t fixloop-sandbox:latest sandbox/
export CONTROL_PLANE_WEBHOOK_SECRET=demo-secret   # the ingress refuses to start without it
```

## Run it

Four terminals.

```bash
# 1 — durable execution engine
temporal server start-dev

# 2 — the stand-in for the repository host
python scripts/fake_host.py

# 3 — the worker (or scripts/worker_scripted.py to avoid model calls)
python -m control_plane.worker

# 4 — the ingress, then the notification
python -m control_plane.ingress &
./scripts/demo_webhook.sh
```

Each demo run adds a commit to the seed so its revision is new. That is deliberate:
`REJECT_DUPLICATE` gives a commit exactly one run *ever*, so without a fresh revision the
second demo on the same engine is deduplicated — correct, and useless to watch.

`demo_webhook.sh` materializes the seed repository, creates a bare clone of it to act as the remote,
signs a payload with the shared secret, posts it, waits for the run, and prints the pull request.

**Expected**:

```text
==> posting 1 signed build-failure notification(s)
    HTTP 202  started

==> waiting for the run

==> the pull request
    #1  fix: failing tests at d080f2e390d0
    http://127.0.0.1:8099/owner/broken-calculator/pull/1
    pull requests for this branch: 1
```

## The property that matters (SC-002, SC-004)

```bash
./scripts/demo_webhook.sh --duplicate 10
```

Sends the same notification ten times, concurrently.

```text
    HTTP 202  started
    HTTP 200  deduplicated        (x9)

    1 started, 9 deduplicated

==> the pull request
    pull requests for this branch: 1
```

The script exits non-zero if more than one delivery starts a run.

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
| SC-006 nothing is merged | `pytest tests/unit/test_host_adapter.py` — an AST check, not a grep |
| SC-010 nothing reaches off-machine | `pytest tests/unit/test_offline_by_default.py` |

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
| `200 deduplicated` on a first run | A run for that sha already exists, and REJECT_DUPLICATE is permanent | The demo makes a fresh commit each run; by hand, use `--force` on the CLI |
| The kill-resume demo says INCONCLUSIVE | Another worker is already serving the task queue and finished the run first | Stop any stray `worker_scripted.py` before running it |
| Run ends `conflicted` | `fix/<sha>` left over from a previous run | The demo script clears it; if run by hand, delete it |
| Pull request never appears | The host stand-in is not running | Terminal 2 |
