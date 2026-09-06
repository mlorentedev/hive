#!/usr/bin/env bash
# Guard: every third-party action is pinned to a commit SHA (mutable tags can be moved;
# several steps here receive repository secrets). Companion of scripts/pin-actions.sh,
# which performs the rewrite. Fails listing each offending line.
# Covers .github/workflows and .gitea/workflows, .yml and .yaml, quoted and unquoted refs,
# and refs followed by an inline comment. Local actions (uses: ./path) and docker:// refs
# are out of scope.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
files=()
while IFS= read -r -d '' f; do files+=("$f"); done < <(
  find "$ROOT/.github/workflows" "$ROOT/.gitea/workflows" -maxdepth 1 -type f \
    \( -name '*.yml' -o -name '*.yaml' \) -print0 2>/dev/null
)
[ "${#files[@]}" -gt 0 ] || { echo "check-actions-pinned: no workflows"; exit 0; }
bad="$(grep -nE 'uses:[[:space:]]+["'"'"']?[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@' "${files[@]}" \
  | grep -vE '@[0-9a-f]{40}["'"'"']?([[:space:]]|$)' \
  | grep -vE 'uses:[[:space:]]+["'"'"']?(\./|docker://)' || true)"
if [ -n "$bad" ]; then
  echo "check-actions-pinned: actions not pinned to a commit SHA (run scripts/pin-actions.sh):"
  printf '%s\n' "$bad" | sed 's/^/  /'
  exit 1
fi
echo "check-actions-pinned: OK (${#files[@]} workflow files)"
