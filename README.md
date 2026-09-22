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

## Status

**Technical POC.** Not production, and not trying to be. PRD Phase 0 is implemented: the
durable loop and the fix branch. The CI webhook trigger, the human approval gate and pull
request creation are later phases and are deliberately out of scope — the scope boundary is
a governance rule in [the constitution](.specify/memory/constitution.md), not a TODO.

## The guarantees, and what enforces them

| Guarantee | Mechanism | Evidence | Trade-off |
|---|---|---|---|
| A run survives its worker dying | Temporal workflow; no activity hands filesystem state to another, so both `apply_patch` and `run_tests` are pure functions of `(revision, diff)` | `scripts/demo_kill_resume.sh`, which exits non-zero unless two workers actually served the run | Two checkouts per attempt instead of one |
| Finished work is not re-billed | The Anthropic client runs with `max_retries=0`, so one history entry is one billed call | `scripts/inspect_history.py <id> --count propose_fix` | A transient 429 costs a whole activity retry rather than a fast in-process one |
| LLM-generated code never reaches the host | One `docker run` with `--network=none --read-only`, a throwaway workspace as the only mount, a non-root uid that means nothing on the host, and no `--env` | `tests/integration/test_sandbox_isolation.py` asserts on the flags *and* on a real container | Dependencies must be baked into the image; a missing one is an error, not a fetch |
| A failing test is never confused with a broken runner | Exit-code table: only `0` and `1` are results; `2`–`5`, `125`–`127`, `137` raise | `tests/unit/test_exit_code_contract.py` | The table is pytest-specific |
| Retrying a writer is safe | Clean checkout before every apply; deterministic branch name with check-if-exists and no overwrite | `test_apply_patch_idempotent.py`, `test_branch_idempotency.py` | Re-running a demo on the same revision ends `conflicted` until you delete the branch |
| The workflow stays deterministic | No I/O, clock or randomness in `workflows/`; imports go through `imports_passed_through` | A replay test against a recorded history, plus an AST test on the import graph | The history must be re-recorded when the activity sequence changes on purpose |

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

```bash
pytest                               # 63 tests; integration needs Docker
ruff check src tests && mypy
```

## Layout

```text
src/control_plane/
├── workflows/     the only deterministic module - no I/O, never imports adapters/
├── activities/    where I/O lives: run_tests, analyze_repo, propose_fix, apply_patch, create_fix_branch
├── adapters/      the three external boundaries: the model, Docker, git
└── domain/        types that cross activity boundaries - no live handles, no paths
sandbox/           the isolation image
seed/              a repository with a deterministic failing test
scripts/           the demos, the history inspector, the seed materializer
specs/001-durable-fix-loop/   spec, plan, research, contracts, tasks
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
- Runs are started by hand. There is no trigger watching anything.
