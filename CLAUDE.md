<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

## Project context

Autonomous Engineering Control Plane — a Python control plane that takes a repository with a failing
test suite and produces a validated fix, durably. PRD: `docs/autonomous-engineering-control-plane-prd.md`.

Spec-driven via spec-kit, **one feature per PRD phase**. Three phases, delivered 0 → 2 → 1.
`001-durable-fix-loop` (Phase 0) is merged. Active feature: `002-ci-trigger-and-pr` (Phase 2).
Read `specs/002-ci-trigger-and-pr/plan.md` before writing code; `.specify/memory/constitution.md`
holds the non-negotiable rules and the locked negative scope.

### Stack

Python 3.12 · `temporalio` (durable orchestration) · `anthropic` (fix proposer, `claude-opus-5`) ·
`pydantic` · Docker CLI via subprocess (sandbox) · `starlette` + `uvicorn` (ingress) ·
`httpx2` (repository host) · `pytest`.

### Rules that are easy to break by accident

- **Workflow code is deterministic.** No I/O, no `datetime.now()`, no `random`, no `uuid` in
  `src/control_plane/workflows/`. Never import `adapters/` from there. Activity and domain imports go
  inside `workflow.unsafe.imports_passed_through()`.
- **A failing test is a result; a broken runner is an error.** `run_tests` returns `TestResult(passed=False)`
  on pytest exit code 1 and raises `TestRunnerError` on everything that is not 0 or 1. Never collapse these.
- **Activities are pure functions of their inputs.** `apply_patch` and `run_tests` each rebuild their own
  workspace from a clean checkout. Never pass a filesystem path from one activity to another — it will not
  survive a resume.
- **Temporal owns retries.** The Anthropic client runs with `max_retries=0`. Do not re-enable it; it would
  hide billed calls from the event history that SC-003 measures.
- **The sandbox has no network.** Dependencies are baked into `sandbox/Dockerfile` at build time. Credentials
  never enter workflow code and never enter the container.
- **Do not write deduplication code.** `workflow_id = fix-<sha>` plus `WorkflowIDReusePolicy.REJECT_DUPLICATE`
  makes Temporal refuse a second run for a commit in any state. The ingress catches the refusal and reports
  the earlier outcome. No dedup table, no cursor, no lock.
- **Verify the signature before parsing the body.** Read it as bytes, check the HMAC with
  `hmac.compare_digest`, and only then parse JSON. Parsing is already acting on untrusted input.
- **Never acknowledge a build you did not start a run for.** If Temporal is unreachable the ingress returns
  503 so the sender retries; its retry queue is the durable one.
- **Everything runs offline.** No network, no token, no CI installation. The repository host is a port with a
  runnable local stand-in (`scripts/fake_host.py`) implementing `specs/002-ci-trigger-and-pr/contracts/host_api.md`.

### Commands

```bash
temporal server start-dev              # durable execution engine
python -m control_plane.worker         # worker
./scripts/demo.sh                      # red -> green end to end
./scripts/demo_kill_resume.sh          # the kill-resume proof
python -m control_plane.ingress        # webhook ingress (separate process)
python scripts/fake_host.py            # local stand-in for the repository host
./scripts/demo_webhook.sh              # failed build -> pull request, offline
pytest                                 # full suite
```
