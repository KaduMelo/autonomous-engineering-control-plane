#!/usr/bin/env bash
# The demo this POC exists for: kill the worker mid-run and watch it resume.
#
# Starts a run, waits until the history shows the third attempt in flight, kills
# the worker, starts a replacement, and reports where execution picked up and how
# many proposals were billed in total.
#
# Requires: a Temporal dev server on localhost:7233, a Docker runtime, the
# sandbox image, and an Anthropic credential.
#
#   temporal server start-dev          # in another terminal
#   ./scripts/demo_kill_resume.sh

set -uo pipefail

export ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
# The scripted harness by default: this demo is about durability, not the model.
WORKER_CMD="${WORKER_CMD:-$ROOT/scripts/worker_scripted.py}"
SEED_DIR="$ROOT/.workspaces/seed"
KILL_AFTER_ATTEMPT="${KILL_AFTER_ATTEMPT:-2}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-5}"

die() { echo "error: $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is required"
docker info >/dev/null 2>&1 || die "the docker daemon is not running"
docker image inspect fixloop-sandbox:latest >/dev/null 2>&1 \
  || die "sandbox image missing - run: docker build -t fixloop-sandbox:latest sandbox/"
"$PY" -c "import temporalio" 2>/dev/null || die "dependencies missing - run: uv pip install -e '.[dev]'"

# Fail here, with a useful message, rather than letting the worker die on
# connect with a stack trace.
"$PY" - <<'PROBE' 2>/dev/null || die "no Temporal server on ${TEMPORAL_TARGET:-localhost:7233} - run: temporal server start-dev"
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.environ["ROOT"], "src"))
from control_plane.worker import connect
asyncio.run(connect())
PROBE

echo "==> materializing the seed repository"
"$ROOT/scripts/seed_repo.sh" "$SEED_DIR" >/dev/null || die "seed setup failed"
SHA="$(git -C "$SEED_DIR" rev-parse HEAD)"
WORKFLOW_ID="fix-$SHA"
echo "    revision $SHA"

# Re-running on the same revision would find the branch from the last run and
# end as conflicted. Clearing it is a deliberate act, which is why it lives here
# and not inside the control plane.
git -C "$SEED_DIR" branch -D "fix/$SHA" >/dev/null 2>&1 && echo "    cleared a leftover fix/<sha>"

cleanup() {
  [[ -n "${WORKER_PID:-}" ]] && kill "$WORKER_PID" 2>/dev/null
  [[ -n "${WORKER2_PID:-}" ]] && kill "$WORKER2_PID" 2>/dev/null
}
trap cleanup EXIT

echo "==> starting worker #1"
"$PY" "$WORKER_CMD" >"$ROOT/.workspaces/worker1.log" 2>&1 &
WORKER_PID=$!
sleep 2
kill -0 "$WORKER_PID" 2>/dev/null || { cat "$ROOT/.workspaces/worker1.log"; die "worker #1 died on startup"; }

echo "==> starting the run (max_attempts=$MAX_ATTEMPTS)"
"$PY" -m control_plane.cli run --repo "$SEED_DIR" --sha "$SHA" \
  --max-attempts "$MAX_ATTEMPTS" >"$ROOT/.workspaces/run.log" 2>&1 &
RUN_PID=$!

echo "==> waiting for this run's id"
RUN_ID=""
for _ in $(seq 1 60); do
  RUN_ID="$(awk '/^run_id/ {print $2}' "$ROOT/.workspaces/run.log" 2>/dev/null)"
  [[ -n "$RUN_ID" ]] && break
  sleep 0.5
done
[[ -n "$RUN_ID" ]] || { cat "$ROOT/.workspaces/run.log"; die "the run never started"; }
echo "    run_id $RUN_ID"

echo "==> waiting for attempt $KILL_AFTER_ATTEMPT to be in flight"
for _ in $(seq 1 240); do
  ATTEMPTS="$("$PY" "$ROOT/scripts/inspect_history.py" "$WORKFLOW_ID" --run-id "$RUN_ID" --count propose_fix 2>/dev/null)"
  [[ "$ATTEMPTS" =~ ^[0-9]+$ ]] || ATTEMPTS=0
  [[ -n "${ATTEMPTS:-}" ]] && echo "    proposals so far: $ATTEMPTS"
  [[ "${ATTEMPTS:-0}" -ge "$KILL_AFTER_ATTEMPT" ]] && break
  sleep 1
done

echo "==> killing worker #1 (pid $WORKER_PID)"
kill -9 "$WORKER_PID" 2>/dev/null
wait "$WORKER_PID" 2>/dev/null
unset WORKER_PID

echo "==> starting worker #2"
"$PY" "$WORKER_CMD" >"$ROOT/.workspaces/worker2.log" 2>&1 &
WORKER2_PID=$!

echo "==> waiting for the run to finish on the replacement worker"
wait "$RUN_PID"
RUN_STATUS=$?
cat "$ROOT/.workspaces/run.log"

echo
echo "==> what the history says"
"$PY" "$ROOT/scripts/inspect_history.py" "$WORKFLOW_ID" --run-id "$RUN_ID"

# A demo that can pass without demonstrating the thing is worse than no demo.
# If only one worker ever served a workflow task, the kill landed after the run
# was already over and nothing about durability was shown.
WORKERS="$("$PY" "$ROOT/scripts/inspect_history.py" "$WORKFLOW_ID" --run-id "$RUN_ID" \
  | awk '/distinct workers/ {print $3}')"
echo
if [[ "${WORKERS:-1}" -lt 2 ]]; then
  echo "INCONCLUSIVE: only $WORKERS worker served this run."
  echo "The kill landed after the run had already finished, so nothing was proven."
  echo "Raise FIXER_DELAY_S (currently ${FIXER_DELAY_S:-8}s) and run again."
  exit 2
fi
echo "PROVEN: $WORKERS workers served this run - it resumed after the kill,"
echo "        and propose_fix was scheduled once per attempt, never re-billed."
exit "$RUN_STATUS"
