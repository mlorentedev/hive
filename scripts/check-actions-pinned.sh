#!/usr/bin/env bash
# Guard: every third-party action is pinned to a commit SHA (mutable tags can be moved;
# several steps here receive repository secrets). Companion of scripts/pin-actions.sh,
# which performs the rewrite. Fails listing each offending line.
# Scope: .github/workflows and .gitea/workflows (.yml/.yaml) plus local composite action
# manifests (.github/actions/*/action.yml), which can themselves invoke third-party
# actions. Only YAML `uses:` mapping keys count (comments and scalar values that happen
# to contain "uses:" do not), and the SHA is validated on the reference itself, never on
# text elsewhere in the line. Local (./) and docker:// references are out of scope.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
files=()
while IFS= read -r -d '' f; do files+=("$f"); done < <(
  { find "$ROOT/.github/workflows" "$ROOT/.gitea/workflows" -maxdepth 1 -type f \
      \( -name '*.yml' -o -name '*.yaml' \) -print0 2>/dev/null
    find "$ROOT/.github/actions" -mindepth 2 -maxdepth 2 -type f \
      \( -name 'action.yml' -o -name 'action.yaml' \) -print0 2>/dev/null; } || true
)
[ "${#files[@]}" -gt 0 ] || { echo "check-actions-pinned: no workflows"; exit 0; }

# A `uses` key: optional indentation, optional "- ", the key, then the reference.
# The reference is captured up to whitespace, a quote or a comment.
# -H: with a single file grep would omit the filename and the sed below would misparse.
bad="$(grep -nHE '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+' "${files[@]}" \
  | sed -E 's/^([^:]+:[0-9]+:)[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+["'"'"']?([^"'"'"'[:space:]#]+).*/\1\3/' \
  | grep -vE ':(\./|docker://)' \
  | grep -vE '@[0-9a-fA-F]{40}$' || true)"
if [ -n "$bad" ]; then
  echo "check-actions-pinned: actions not pinned to a commit SHA (run scripts/pin-actions.sh):"
  printf '%s\n' "$bad" | sed 's/^/  /'
  exit 1
fi
echo "check-actions-pinned: OK (${#files[@]} workflow files)"
