# Autonomous Engineering Control Plane

A Python control plane that takes a repository with a failing test suite and produces a
validated fix — durably. Point it at a repo and a commit; it runs the suite in a sandbox to
see what is broken, iterates propose → apply → validate inside an attempt budget, and leaves
the winning change on a deterministic branch.

The interesting part is not that an LLM can fix a test. It is that **killing the worker
mid-run costs nothing**: the run resumes where it stopped, and the model calls already paid
for are not paid for again.

```text
$ ./scripts/demo_kill_resume.sh

==> waiting for attempt 2 to be in flight
==> killing worker #1 (pid 298424)
==> starting worker #2

status     fixed
attempts   3
  1. still red - subtract the percentage scaled down
  2. still red - subtract the percentage scaled up
  3. green - treat percent as a percentage of the price
branch     fix/4b1a0d431c4e @ 314a4cb600b7

distinct workers    2
  -> the run was served by more than one worker: it survived a restart
SC-003: propose_fix scheduled 3 time(s).

PROVEN: 2 workers served this run - it resumed after the kill,
        and propose_fix was scheduled once per attempt, never re-billed.
```

A failed build can also drive the whole thing with nobody watching:

```text
$ ./scripts/demo_webhook.sh --duplicate 10

==> posting 10 signed build-failure notification(s)
    HTTP 202  started
    HTTP 200  deduplicated          (x9)

    1 started, 9 deduplicated

==> the pull request
    #1  fix: failing tests at d080f2e390d0
    pull requests for this branch: 1
```

Ten deliveries, one run, one pull request — and **there is no deduplication code in this
repository**. `workflow_id = fix-<sha>` plus `REJECT_DUPLICATE` makes the engine refuse the
second start; the ingress just turns that refusal into an answer.

Everything above runs offline: no network, no token, no CI installation, no hosted
repository.

## Status

**Technical POC.** Not production, and not trying to be. PRD Phases 0 and 2 are implemented:
the durable loop, the fix branch, the webhook trigger and the pull request. The human
approval gate is Phase 1 and is next. Everything else — OTel, LangGraph, MCP, multi-provider
adapters — is out of scope with a written reason in the PRD, not a TODO.

## The guarantees, and what enforces them

| Guarantee | Mechanism | Evidence | Trade-off |
|---|---|---|---|
| A run survives its worker dying | Temporal workflow; no activity hands filesystem state to another, so both `apply_patch` and `run_tests` are pure functions of `(revision, diff)` | `scripts/demo_kill_resume.sh`, which exits non-zero unless two workers actually served the run | Two checkouts per attempt instead of one |
| Finished work is not re-billed | The Anthropic client runs with `max_retries=0`, so one history entry is one billed call | `scripts/inspect_history.py <id> --count propose_fix` | A transient 429 costs a whole activity retry rather than a fast in-process one |
| LLM-generated code never reaches the host | One `docker run` with `--network=none --read-only`, a throwaway workspace as the only mount, a non-root uid that means nothing on the host, and no `--env` | `tests/integration/test_sandbox_isolation.py` asserts on the flags *and* on a real container | Dependencies must be baked into the image; a missing one is an error, not a fetch |
| A failing test is never confused with a broken runner | Exit-code table: only `0` and `1` are results; `2`–`5`, `125`–`127`, `137` raise | `tests/unit/test_exit_code_contract.py` | The table is pytest-specific |
| Retrying a writer is safe | Clean checkout before every apply; deterministic branch name with check-if-exists and no overwrite | `test_apply_patch_idempotent.py`, `test_branch_idempotency.py` | Re-running a demo on the same revision ends `conflicted` until you delete the branch |
| The workflow stays deterministic | No I/O, clock or randomness in `workflows/`; imports go through `imports_passed_through` | A replay test against a recorded history, plus an AST test on the import graph | The history must be re-recorded when the activity sequence changes on purpose |
| One broken commit gets exactly one run | `workflow_id = fix-<sha>` with `REJECT_DUPLICATE`; the engine refuses a second start in any state | Ten concurrent deliveries produce one run, in a test and in the demo | A commit can never be retried — re-running one means `--force`, which terminates the prior run |
| A notification is acted on only if it is authentic | HMAC over the raw body, `compare_digest`, verified before the body is parsed | An AST test asserts verification precedes parsing, and that no digest is compared with `==` | A shared secret is symmetric: whoever can verify can also forge |
| Exactly one pull request per fix | Deterministic branch, check-before-create, and the host's "already exists" treated as success | Ten calls produce one pull request and one `created=True` | An extra API call per run |
| Nothing is ever merged | No merge path exists in the adapter, and no merge scope is requested | An AST test fails if a merge-named function or a `/merge` path appears | None |

## Run it

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
docker build -t fixloop-sandbox:latest sandbox/

temporal server start-dev            # terminal 1
python -m control_plane.worker       # terminal 2 - calls the model
./scripts/demo.sh                    # terminal 3 - red repo to green branch
```

The kill-resume demo runs its own workers and **does not call the model** — durability is
what it proves, and billing tokens to demonstrate something unrelated to them is waste:

```bash
./scripts/demo_kill_resume.sh
```

The webhook demo needs two more processes and a secret:

```bash
export CONTROL_PLANE_WEBHOOK_SECRET=demo-secret
python scripts/fake_host.py          # the local stand-in for the repository host
python -m control_plane.ingress      # the webhook endpoint
./scripts/demo_webhook.sh            # failed build -> pull request
```

```bash
pytest                               # 141 tests; integration needs Docker
ruff check src tests && mypy
```

## Layout

```text
src/control_plane/
├── workflows/     the only deterministic module - no I/O, never imports adapters/
├── activities/    where I/O lives: run_tests, analyze_repo, propose_fix, apply_patch, create_fix_branch
├── ingress/       the webhook endpoint - a separate process that holds no state
├── adapters/      the external boundaries: the model, Docker, git, the repository host
└── domain/        types that cross activity boundaries - no live handles, no paths
sandbox/           the isolation image
seed/              a repository with a deterministic failing test
scripts/           the demos, the history inspector, the seed materializer
specs/                        spec, plan, research, contracts and tasks per feature
```

Design documents are not decoration here: [`spec.md`](specs/001-durable-fix-loop/spec.md) has
the requirements and their acceptance criteria,
[`research.md`](specs/001-durable-fix-loop/research.md) records each decision as
Guarantee → Mechanism → Evidence → Trade-off, and
[`plan.md`](specs/001-durable-fix-loop/plan.md) carries the constitution check with the two
decisions that cost something written out rather than hidden.

## Known limits

- One seed repository, Python, one single-file logic defect. Multi-language and multi-file
  failures are out of scope.
- `FR-018` lets the fixer edit test files. A green suite is therefore **not proof on its own** —
  deleting a test also turns it green. The mitigation is that the winning diff is preserved in
  the run record for a human to read, which is a deliberate trade-off, not an oversight.
- **The real repository-host adapter is never exercised.** A pull request is an API concept,
  not a git one, so an offline run proves this side of the contract — one request, idempotent,
  with the right contents — and not that GitHub accepts it. Both sides implement
  [the written contract](specs/002-ci-trigger-and-pr/contracts/host_api.md), so a divergence
  is a documentation bug rather than a surprise.
- There is no human approval gate yet. A validated fix goes straight to a pull request —
  which is a request for review, and nothing merges it.
