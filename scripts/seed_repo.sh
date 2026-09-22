#!/usr/bin/env bash
# Materialize seed/broken-calculator/ as a git repository at a reproducible commit.
#
# The seed is stored as plain files so it does not become an embedded git
# repository inside the control plane's own repo. Author and committer identity
# and dates are pinned, so the commit sha is the same on every machine - the
# demo and the recorded Temporal histories both depend on that.
#
# Usage: scripts/seed_repo.sh [destination]   (default: .workspaces/seed)
# Prints the destination path and the commit sha.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_SRC="$REPO_ROOT/seed/broken-calculator"
DEST="${1:-$REPO_ROOT/.workspaces/seed}"

FIXED_DATE="2026-01-01T00:00:00+00:00"

rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SEED_SRC/." "$DEST/"

cd "$DEST"
git init --quiet --initial-branch=main
git add -A
GIT_AUTHOR_NAME="Seed" \
GIT_AUTHOR_EMAIL="seed@example.invalid" \
GIT_AUTHOR_DATE="$FIXED_DATE" \
GIT_COMMITTER_NAME="Seed" \
GIT_COMMITTER_EMAIL="seed@example.invalid" \
GIT_COMMITTER_DATE="$FIXED_DATE" \
  git commit --quiet -m "Initial commit"

echo "path=$DEST"
echo "sha=$(git rev-parse HEAD)"
