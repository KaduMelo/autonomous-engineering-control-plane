#!/usr/bin/env bash
# Failed build -> pull request, with nobody watching. Entirely offline.
#
# Needs four things running:
#   temporal server start-dev                      # terminal 1
#   python scripts/fake_host.py                    # terminal 2
#   python -m control_plane.worker                 # terminal 3
#   python -m control_plane.ingress                # terminal 3
#
# Then:
#   ./scripts/demo_webhook.sh                # one notification
#   ./scripts/demo_webhook.sh --duplicate 10 # ten at once; still one run

set -uo pipefail

export ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
SEED_DIR="$ROOT/.workspaces/seed"
REMOTE="$ROOT/.workspaces/remote.git"
FULL_NAME="${FULL_NAME:-owner/broken-calculator}"
TIMES=1
[[ "${1:-}" == "--duplicate" ]] && TIMES="${2:-10}"

die() { echo "error: $*" >&2; exit 1; }

: "${CONTROL_PLANE_WEBHOOK_SECRET:?set CONTROL_PLANE_WEBHOOK_SECRET, the same value the ingress uses}"
command -v docker >/dev/null || die "docker is required"
docker image inspect fixloop-sandbox:latest >/dev/null 2>&1 \
  || die "sandbox image missing - run: docker build -t fixloop-sandbox:latest sandbox/"
curl -fsS -X POST "${CONTROL_PLANE_HOST_API:-http://127.0.0.1:8099}/_reset" >/dev/null 2>&1 \
  || die "the host stand-in is not running - run: python scripts/fake_host.py"

echo "==> building the seed repository and its remote"
"$ROOT/scripts/seed_repo.sh" "$SEED_DIR" >/dev/null || die "seed setup failed"

# Give this demo run its own commit. The seed's sha is deterministic on purpose -
# the recorded histories depend on it - but REJECT_DUPLICATE means one commit
# gets exactly one run, ever. Without a fresh sha the second demo on the same
# engine is deduplicated, which is correct and useless to watch.
printf '\n<!-- demo run %s -->\n' "$(date -u +%Y%m%dT%H%M%S)" >> "$SEED_DIR/README.md"
GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.invalid \
GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.invalid \
  git -C "$SEED_DIR" commit --quiet -am "demo run" || die "could not create the demo commit"
SHA="$(git -C "$SEED_DIR" rev-parse HEAD)"
rm -rf "$REMOTE"
git clone --bare --quiet "$SEED_DIR" "$REMOTE" || die "could not create the bare remote"
# The clone cache must not carry a fix branch from an earlier demo, or the run
# ends `conflicted` - which is correct behaviour, and confusing in a demo.
rm -rf "$ROOT/.workspaces/clones"
echo "    revision $SHA"
echo "    remote   $REMOTE"

echo
echo "==> posting $TIMES signed build-failure notification(s)"
"$PY" "$ROOT/scripts/send_webhook.py" \
  --repo-url "$REMOTE" --full-name "$FULL_NAME" --revision "$SHA" --times "$TIMES"
POST_STATUS=$?
[[ $POST_STATUS -eq 0 ]] || die "the ingress did not accept the notification"

echo
echo "==> waiting for the run"
for _ in $(seq 1 120); do
  OUT="$("$PY" "$ROOT/scripts/inspect_history.py" "fix-$SHA" 2>/dev/null)" && \
    echo "$OUT" | grep -q "open_pr" && break
  sleep 2
done

echo
echo "==> the pull request"
curl -fsS "${CONTROL_PLANE_HOST_API:-http://127.0.0.1:8099}/repos/$FULL_NAME/pulls?head=owner:fix/$SHA&state=open" \
  | "$PY" -c "
import json, sys
pulls = json.load(sys.stdin)
if not pulls:
    print('    none - the run did not reach green'); raise SystemExit(1)
for p in pulls:
    print(f\"    #{p['number']}  {p['title']}\")
    print(f\"    {p['html_url']}\")
print(f'    pull requests for this branch: {len(pulls)}')
raise SystemExit(0 if len(pulls) == 1 else 1)
"
