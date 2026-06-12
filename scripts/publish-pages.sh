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

FORCE="${1:-}"
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"
echo "Building static site..."
"$PY" scripts/export_static.py --out "$SITE"
touch "$SITE/.nojekyll"   # serve files as-is (no Jekyll processing)

# --- Regression guard ---
# This force-pushes a snapshot built from the LOCAL DB, which can be stale or
# diverged from what the hourly cron has since published (and OTS-anchored).
# Abort unless this export matches-or-extends the live history; --force overrides.
if [ "$FORCE" = "--force" ]; then
  echo "WARNING: --force given -- skipping the published-history guard (anchors may be contradicted)."
elif live_head="$(curl -fsS https://lucky2049.com/head.json 2>/dev/null)"; then
  guard_rc=0
  printf '%s' "$live_head" | "$PY" - "$SITE/index.json" <<'PYGUARD' || guard_rc=$?
import json, sys
live = json.load(sys.stdin)
did, head = live.get("draw_id"), live.get("head")
if did is None or did < 0:
    sys.exit(0)  # nothing published yet -- nothing to regress
byid = {d["id"]: d for d in json.load(open(sys.argv[1])).get("draws", [])}
rec = byid.get(did)
# The export must contain the live head draw with the SAME commitment; since the
# commitment chains, matching there means 0..did agree, and the export may extend.
sys.exit(0 if rec and rec.get("commitment") == head else 1)
PYGUARD
  if [ "$guard_rc" -ne 0 ]; then
    echo "ABORT: this export does not match-or-extend the published history (https://lucky2049.com/head.json)." >&2
    echo "       Force-pushing would REGRESS/DIVERGE and contradict the OpenTimestamps anchors in anchors/." >&2
    echo "       The hourly cron is the single publish source; re-run with --force only if you are certain." >&2
    exit 1
  fi
  echo "Guard OK: export matches-or-extends the published head."
else
  echo "No live head.json reachable (first deploy?) -- skipping the regression guard."
fi

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
