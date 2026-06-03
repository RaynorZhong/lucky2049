#!/usr/bin/env bash
#
# Build the static snapshot and publish it to the gh-pages branch (GitHub Pages).
# Run where the DB lives. Uses your existing git auth (SSH). ASCII only.
#
# One-time setup after the first run: enable Pages from the gh-pages branch --
#   Settings > Pages > Source: "Deploy from a branch" > gh-pages / (root)
#   or:  gh api -X POST repos/<owner>/<repo>/pages -f source[branch]=gh-pages -f source[path]=/
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
SITE="$REPO_ROOT/site"
BRANCH="gh-pages"

PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"
echo "Building static site..."
"$PY" scripts/export_static.py --out "$SITE"
touch "$SITE/.nojekyll"   # serve files as-is (no Jekyll processing)

REMOTE="$(git remote get-url origin)"
TMP="$(mktemp -d)"
cp -R "$SITE"/. "$TMP"/
cd "$TMP"
git init -q
git checkout -q -b "$BRANCH"
git add -A
git -c user.email=pages@lucky2049 -c user.name=lucky2049-pages commit -q -m "Publish static site snapshot"
echo "Force-pushing $BRANCH to $REMOTE ..."
git push -f -q "$REMOTE" "$BRANCH"
cd "$REPO_ROOT"; rm -rf "$TMP"

OWNER_REPO="$(echo "$REMOTE" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
echo "Done. Once Pages is enabled for the $BRANCH branch, the site is at:"
echo "  https://$(echo "$OWNER_REPO" | cut -d/ -f1).github.io/$(echo "$OWNER_REPO" | cut -d/ -f2)/"
