<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

## Project context

Autonomous Engineering Control Plane — a Python control plane that takes a repository with a failing
test suite and produces a validated fix, durably. PRD: `docs/autonomous-engineering-control-plane-prd.md`.

Spec-driven via spec-kit, **one feature per PRD phase**. Active feature: `001-durable-fix-loop`
(PRD Phase 0). Read `specs/001-durable-fix-loop/plan.md` before writing code; `.specify/memory/constitution.md`
holds the non-negotiable rules and the locked negative scope.

### Stack

Python 3.12 · `temporalio` (durable orchestration) · `anthropic` (fix proposer, `claude-opus-5`) ·
`pydantic` · Docker CLI via subprocess (sandbox) · `pytest`.

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

### Commands

```bash
temporal server start-dev              # durable execution engine
python -m control_plane.worker         # worker
./scripts/demo.sh                      # red -> green end to end
./scripts/demo_kill_resume.sh          # the kill-resume proof
pytest                                 # full suite
```
