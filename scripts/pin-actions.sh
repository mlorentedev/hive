#!/usr/bin/env bash
# Rewrite `uses: owner/repo@vX[.Y[.Z]]` to `uses: owner/repo@<commit-sha> # vX[.Y[.Z]]`
# for every workflow under .github/workflows and .gitea/workflows. Resolves each tag
# through the GitHub API and dereferences annotated tags to the commit. Idempotent:
# lines already carrying a 40-hex SHA are left alone. Prints each rewrite.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
files=$(ls "$ROOT"/.github/workflows/*.yml "$ROOT"/.github/workflows/*.yaml "$ROOT"/.gitea/workflows/*.yml 2>/dev/null || true)
[ -n "$files" ] || { echo "pin-actions: no workflows"; exit 0; }
declare -A cache
resolve() { # owner/repo tag -> commit sha
  local repo="$1" tag="$2" key="$1@$2"
  if [ -n "${cache[$key]:-}" ]; then echo "${cache[$key]}"; return; fi
  local obj type sha
  obj=$(gh api "repos/$repo/git/ref/tags/$tag" --jq '.object | "\(.type) \(.sha)"' 2>/dev/null) || { echo ""; return; }
  type=${obj%% *}; sha=${obj##* }
  if [ "$type" = "tag" ]; then sha=$(gh api "repos/$repo/git/tags/$sha" --jq '.object.sha'); fi
  cache[$key]="$sha"; echo "$sha"
}
rc=0
for f in $files; do
  while IFS= read -r line; do
    ref=$(printf '%s' "$line" | grep -oE 'uses: [A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@v[0-9][0-9.]*[[:space:]]*$' | sed 's/uses: //; s/[[:space:]]*$//') || true
    [ -n "$ref" ] || continue
    full=${ref%@*}; tag=${ref##*@}; repo=$(printf '%s' "$full" | cut -d/ -f1-2)
    sha=$(resolve "$repo" "$tag")
    if [ -z "$sha" ]; then echo "pin-actions: could not resolve $ref in $f"; rc=1; continue; fi
    REF="$ref" FULL="$full" SHA="$sha" TAG="$tag" perl -pi -e 's/uses: \Q$ENV{REF}\E\s*$/uses: $ENV{FULL}\@$ENV{SHA} # $ENV{TAG}\n/' "$f"
    echo "pinned $f: $ref -> $sha"
  done < "$f"
done
exit $rc
