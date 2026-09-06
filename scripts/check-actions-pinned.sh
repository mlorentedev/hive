#!/usr/bin/env bash
# Guard: every third-party action is pinned to a commit SHA (mutable tags can be moved;
# several steps here receive repository secrets). Companion of scripts/pin-actions.sh,
# which performs the rewrite. Fails listing each offending line.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
files=$(ls "$ROOT"/.github/workflows/*.yml "$ROOT"/.github/workflows/*.yaml "$ROOT"/.gitea/workflows/*.yml 2>/dev/null || true)
[ -n "$files" ] || { echo "check-actions-pinned: no workflows"; exit 0; }
bad=$(grep -nE 'uses:[[:space:]]+[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@' $files | grep -vE '@[0-9a-f]{40}([[:space:]]|$)' | grep -vE 'uses:[[:space:]]+\./' || true)
if [ -n "$bad" ]; then
  echo "check-actions-pinned: actions not pinned to a commit SHA (run scripts/pin-actions.sh):"
  printf '%s\n' "$bad" | sed 's/^/  /'
  exit 1
fi
echo "check-actions-pinned: OK"
