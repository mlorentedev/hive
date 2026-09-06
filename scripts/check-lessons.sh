#!/usr/bin/env bash
# Guard: docs/lessons/ numbering and index integrity.
# 1. No two lesson files share a number (the collision that hit web#326, dotfiles#1519, kubelab).
# 2. Every lesson file is listed in its _index.md and every indexed file exists.
# Lessons may live flat in docs/lessons/ or one level down in category directories
# (docs/lessons/<category>/ with a per-category _index.md); both layouts are checked.
# Runs under bash and zsh; no globs (an unmatched glob aborts under zsh NOMATCH), and
# filenames are handled NUL-separated so spaces cannot split them.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LESSONS_DIR="$ROOT/docs/lessons"
[ -d "$LESSONS_DIR" ] || { echo "check-lessons: docs/lessons/ not present, nothing to check"; exit 0; }
rc=0
total=0

# --- 1. duplicate numbers across the whole tree (a number is unique per repo) ---
dupes="$(find "$LESSONS_DIR" -maxdepth 2 -type f -name 'lesson-[0-9]*.md' -printf '%f\n' \
  | grep -oE '^lesson-[0-9]+' | sort | uniq -d || true)"
if [ -n "$dupes" ]; then
  echo "check-lessons: lesson numbers used more than once:"
  while IFS= read -r n; do
    find "$LESSONS_DIR" -maxdepth 2 -type f -name "${n}-*.md" -printf '  %P\n'
  done <<< "$dupes"
  rc=1
fi

# --- 2. index integrity, per directory that holds lesson files ---
while IFS= read -r -d '' dir; do
  index="$dir/_index.md"
  count=0
  while IFS= read -r -d '' f; do
    count=$((count + 1))
    name="$(basename "$f")"
    if [ -f "$index" ]; then
      # Match the filename as a whole token (followed by ) | ] or end), not as a substring,
      # so lesson-12.md.bak cannot satisfy lesson-12.md.
      grep -qE "(^|[^A-Za-z0-9._-])$(printf '%s' "$name" | sed 's/[.[\*^$]/\\&/g')([^A-Za-z0-9._-]|$)" "$index" \
        || { echo "check-lessons: not in ${index#"$ROOT"/}: $name"; rc=1; }
    fi
  done < <(find "$dir" -maxdepth 1 -type f -name 'lesson-[0-9]*.md' -print0)
  [ "$count" -gt 0 ] || continue
  total=$((total + count))
  if [ -f "$index" ]; then
    while IFS= read -r f; do
      [ -f "$dir/$f" ] || { echo "check-lessons: indexed in ${index#"$ROOT"/} but missing: $f"; rc=1; }
    done < <(grep -oE 'lesson-[0-9]+[A-Za-z0-9._-]*\.md' "$index" | sort -u)
  else
    echo "check-lessons: ${dir#"$ROOT"/} has lessons but no _index.md"
    rc=1
  fi
done < <(find "$LESSONS_DIR" -maxdepth 1 -type d -print0)

[ "$rc" -eq 0 ] && echo "check-lessons: OK ($total lessons)"
exit $rc
