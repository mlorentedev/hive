#!/usr/bin/env bash
# Guard: docs/lessons/ numbering and index integrity.
# 1. No two lesson files share a number (the collision that hit web#326, dotfiles#1519, kubelab).
# 2. Every lesson file is listed in _index.md and every indexed file exists.
# Runs under bash and zsh; no globs (an unmatched glob aborts under zsh NOMATCH).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LESSONS_DIR="$ROOT/docs/lessons"
INDEX="$LESSONS_DIR/_index.md"
[ -d "$LESSONS_DIR" ] || { echo "check-lessons: docs/lessons/ not present, nothing to check"; exit 0; }
rc=0
dupes="$(ls "$LESSONS_DIR" | grep -oE '^lesson-[0-9]+' | sort | uniq -d || true)"
if [ -n "$dupes" ]; then
  echo "check-lessons: lesson numbers used more than once:"
  for n in $dupes; do ls "$LESSONS_DIR" | grep -E "^${n}-" | sed 's/^/  /'; done
  rc=1
fi
if [ -f "$INDEX" ]; then
  for f in $(ls "$LESSONS_DIR" | grep -E '^lesson-[0-9]+.*\.md$'); do
    grep -qF "$f" "$INDEX" || { echo "check-lessons: not in _index.md: $f"; rc=1; }
  done
  for f in $(grep -oE 'lesson-[0-9]+[A-Za-z0-9._-]*\.md' "$INDEX" | sort -u); do
    [ -f "$LESSONS_DIR/$f" ] || { echo "check-lessons: indexed but missing: $f"; rc=1; }
  done
else
  echo "check-lessons: no _index.md, only checking duplicates"
fi
[ "$rc" -eq 0 ] && echo "check-lessons: OK ($(ls "$LESSONS_DIR" | grep -cE '^lesson-[0-9]+') lessons)"
exit $rc
