#!/usr/bin/env bash
# Rewrite `uses: owner/repo@<tag>` to `uses: owner/repo@<commit-sha> # <tag>` for every
# workflow under .github/workflows and .gitea/workflows (.yml and .yaml). Resolves each
# tag through the GitHub API and dereferences annotated tags to the commit; a ref that is
# a branch (e.g. release/v1) is resolved to the branch head and marked as such in the
# trailer. Idempotent: lines already carrying a 40-hex SHA are left alone. Quoted refs and
# inline comments are preserved. Resolutions are cached for the run (one API call per
# distinct owner/repo@tag, not per occurrence). Prints each rewrite.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
files=()
while IFS= read -r -d '' f; do files+=("$f"); done < <(
  find "$ROOT/.github/workflows" "$ROOT/.gitea/workflows" -maxdepth 1 -type f \
    \( -name '*.yml' -o -name '*.yaml' \) -print0 2>/dev/null
)
[ "${#files[@]}" -gt 0 ] || { echo "pin-actions: no workflows"; exit 0; }

declare -A cache
RESOLVED=""
resolve() { # sets RESOLVED to "<sha> <kind>" (kind: tag|branch) or "" when unresolvable
  local repo="$1" ref="$2" key="$1@$2" obj type sha
  RESOLVED=""
  if [ -n "${cache[$key]:-}" ]; then RESOLVED="${cache[$key]}"; return; fi
  if obj=$(gh api "repos/$repo/git/ref/tags/$ref" --jq '.object | "\(.type) \(.sha)"' 2>/dev/null); then
    type=${obj%% *}; sha=${obj##* }
    [ "$type" = "tag" ] && sha=$(gh api "repos/$repo/git/tags/$sha" --jq '.object.sha')
    RESOLVED="$sha tag"
  elif sha=$(gh api "repos/$repo/git/ref/heads/$ref" --jq '.object.sha' 2>/dev/null); then
    RESOLVED="$sha branch"
  fi
  [ -n "$RESOLVED" ] && cache[$key]="$RESOLVED"
}

rc=0
for f in "${files[@]}"; do
  # Candidate refs: not already a 40-hex SHA, not local, not docker.
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    full=${ref%@*}; tag=${ref##*@}; repo=$(printf '%s' "$full" | cut -d/ -f1-2)
    resolve "$repo" "$tag"
    if [ -z "$RESOLVED" ]; then echo "pin-actions: could not resolve $ref in ${f#"$ROOT"/}"; rc=1; continue; fi
    sha=${RESOLVED%% *}; kind=${RESOLVED##* }
    trailer="$tag"; [ "$kind" = "branch" ] && trailer="$tag (branch head, re-pin deliberately)"
    REF="$ref" FULL="$full" SHA="$sha" TRAILER="$trailer" perl -pi -e '
      chomp;
      s{(uses:\h+)(["\x27]?)\Q$ENV{REF}\E\2\h*(#[^\n]*)?$}{
        my ($lead,$q,$c)=($1,$2,$3);
        my $tr = $c ? $c : "# $ENV{TRAILER}";
        "$lead$q$ENV{FULL}\@$ENV{SHA}$q  $tr"
      }e;
      $_ .= "\n"' "$f"
    echo "pinned ${f#"$ROOT"/}: $ref -> $sha ($kind)"
  done < <(grep -oE 'uses:[[:space:]]+["'"'"']?[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[A-Za-z0-9_./-]+' "$f" \
    | sed -E 's/^uses:[[:space:]]+["'"'"']?//' \
    | grep -vE '@[0-9a-f]{40}$' | grep -vE '^(\./|docker://)' | sort -u)
done
exit $rc
