#!/bin/sh
# Keep the copy of the app inside the Home Assistant app folder (ha-addon/app) identical to app/.
#
# A Home Assistant app is built from its own folder only, so Docker cannot reach ../app.
# The bundle is therefore committed, and this script keeps it in step.
#
#   scripts/sync_addon.sh            copy app/ to ha-addon/app/
#   scripts/sync_addon.sh --check    change nothing; exit 1 if the bundle differs from app/
#
# An optional last argument is the project root (default: the folder above this script).
# Plain POSIX sh with only cp, find, diff and mktemp, so it runs in a minimal container.
set -eu

MODE=sync
if [ "${1:-}" = "--check" ]; then
    MODE=check
    shift
fi
ROOT=${1:-$(cd "$(dirname "$0")/.." && pwd)}
SRC="$ROOT/app"
DEST="$ROOT/ha-addon/app"

if [ ! -d "$SRC" ]; then
    echo "sync_addon: no app directory at $SRC" >&2
    exit 2
fi

# Copy a folder without Python's cache files, which are never part of the bundle.
clean_copy() {
    mkdir -p "$2"
    cp -R "$1/." "$2/"
    find "$2" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
    find "$2" -type f \( -name '*.pyc' -o -name '*.pyo' \) -exec rm -f {} + 2>/dev/null || true
}

if [ "$MODE" = "sync" ]; then
    rm -rf "$DEST"
    clean_copy "$SRC" "$DEST"
    echo "sync_addon: copied app/ to ha-addon/app/ ($(find "$DEST" -type f | wc -l | tr -d ' ') files)"
    exit 0
fi

if [ ! -d "$DEST" ]; then
    echo "sync_addon: ha-addon/app does not exist; run scripts/sync_addon.sh" >&2
    exit 1
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM
clean_copy "$SRC" "$TMP/src"
clean_copy "$DEST" "$TMP/bundle"
if diff -r -q "$TMP/src" "$TMP/bundle" >"$TMP/report" 2>&1; then
    echo "sync_addon: ha-addon/app matches app/"
    exit 0
fi
sed -e "s#$TMP/src#app#g" -e "s#$TMP/bundle#ha-addon/app#g" "$TMP/report" >&2
echo "sync_addon: ha-addon/app is out of date. Run scripts/sync_addon.sh and commit the result." >&2
exit 1
