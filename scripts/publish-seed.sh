#!/usr/bin/env bash
#
# Publish the cold-start seed CSV (data/blockchain_timeup898560.csv) as a GitHub
# Release asset, so fresh deployments can fetch it via LOTTO_SEED_CSV_URL instead
# of carrying the 82MB file in the repo. See docs/DEPLOY.md.
#
# Requires the gh CLI, authenticated:  gh auth login   (or set GH_TOKEN / GITHUB_TOKEN)
# Run from the repo root, on a machine that has the local CSV.
#
# ASCII only: a non-ASCII char next to "$TAG" tripped `set -u` ("unbound variable").
#
set -euo pipefail

TAG="seed-v1"
FILE="data/blockchain_timeup898560.csv"
REPO="RaynorZhong/lucky2049"
TITLE="Cold-start seed dataset (Bitcoin block hashes)"

command -v gh >/dev/null 2>&1 || { echo "error: gh CLI not found - install with 'brew install gh', then 'gh auth login'"; exit 1; }
[ -f "$FILE" ] || { echo "error: $FILE not found - run from the repo root with the local CSV present"; exit 1; }

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo "Release $TAG exists; uploading asset (clobber)..."
  gh release upload "$TAG" "$FILE" --repo "$REPO" --clobber
else
  echo "Creating release $TAG ..."
  gh release create "$TAG" "$FILE" --repo "$REPO" --title "$TITLE" \
    --notes "Bitcoin block-hash CSV used to seed a fresh database (create_bitcoin). Point LOTTO_SEED_CSV_URL at the asset URL below."
fi

URL="https://github.com/$REPO/releases/download/$TAG/$(basename "$FILE")"
echo
echo "Done. For new deployments set:"
echo "  export LOTTO_SEED_CSV_URL=\"$URL\""
echo "sha256: $(shasum -a 256 "$FILE" | awk '{print $1}')"
