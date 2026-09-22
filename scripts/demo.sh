#!/usr/bin/env bash
# Red repository -> green fix on a branch, end to end.
#
# Expects a Temporal dev server and a worker already running:
#
#   temporal server start-dev          # terminal 1
#   python -m control_plane.worker     # terminal 2  (calls the model)
#   ./scripts/demo.sh                  # terminal 3
#
# To run it without spending tokens, use the scripted worker in terminal 2:
#   .venv/bin/python scripts/worker_scripted.py

set -uo pipefail

export ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
SEED_DIR="$ROOT/.workspaces/seed"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-5}"

die() { echo "error: $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is required"
docker info >/dev/null 2>&1 || die "the docker daemon is not running"
docker image inspect fixloop-sandbox:latest >/dev/null 2>&1 \
  || die "sandbox image missing - run: docker build -t fixloop-sandbox:latest sandbox/"

"$PY" - <<'PROBE' 2>/dev/null || die "no Temporal server on ${TEMPORAL_TARGET:-localhost:7233} - run: temporal server start-dev"
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.environ["ROOT"], "src"))
from control_plane.worker import connect
asyncio.run(connect())
PROBE

echo "==> materializing the seed repository"
"$ROOT/scripts/seed_repo.sh" "$SEED_DIR" >/dev/null || die "seed setup failed"
SHA="$(git -C "$SEED_DIR" rev-parse HEAD)"
echo "    revision $SHA"

# Re-running on the same revision would find the branch from the last run and
# end as `conflicted` rather than overwriting it. Clearing it is a deliberate
# human act, which is why it lives in the demo script and not in the control
# plane.
if git -C "$SEED_DIR" branch -D "fix/$SHA" >/dev/null 2>&1; then
  echo "    cleared a leftover fix/<sha> from a previous run"
fi

echo "==> repository state before the run"
BEFORE_HEAD="$(git -C "$SEED_DIR" rev-parse HEAD)"
BEFORE_BRANCH="$(git -C "$SEED_DIR" rev-parse --abbrev-ref HEAD)"
echo "    on $BEFORE_BRANCH at ${BEFORE_HEAD:0:12}"

echo "==> starting the run (max_attempts=$MAX_ATTEMPTS)"
"$PY" -m control_plane.cli run --repo "$SEED_DIR" --sha "$SHA" --max-attempts "$MAX_ATTEMPTS"
RUN_STATUS=$?

echo
echo "==> the fix"
if git -C "$SEED_DIR" rev-parse --verify --quiet "fix/$SHA" >/dev/null; then
  git -C "$SEED_DIR" --no-pager log --oneline -1 "fix/$SHA"
  git -C "$SEED_DIR" --no-pager diff "$SHA" "fix/$SHA" | sed 's/^/    /'
else
  echo "    no fix branch - the run did not reach green"
fi

echo
echo "==> your repository is where you left it (SC-011)"
AFTER_HEAD="$(git -C "$SEED_DIR" rev-parse HEAD)"
AFTER_BRANCH="$(git -C "$SEED_DIR" rev-parse --abbrev-ref HEAD)"
echo "    on $AFTER_BRANCH at ${AFTER_HEAD:0:12}"
if [[ "$AFTER_HEAD" == "$BEFORE_HEAD" && "$AFTER_BRANCH" == "$BEFORE_BRANCH" ]] \
   && [[ -z "$(git -C "$SEED_DIR" status --porcelain)" ]]; then
  echo "    HEAD, branch and working tree unchanged"
else
  echo "    WARNING: the run moved something it should not have"
  exit 3
fi

exit "$RUN_STATUS"
