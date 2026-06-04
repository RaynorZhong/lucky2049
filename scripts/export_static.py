#!/usr/bin/env python3
"""
Export a static, self-verifying snapshot of the draw history into site/.

Reads the local database and writes a small static site (no 144-hash blobs, so
the snapshot stays ~1-2MB):
  site/index.json  - {count, head, algo_version, draws:[slim records]}
  site/head.json   - the commitment head
  site/index.html, site/verify.html, site/verify.js, site/style.css

Each slim record has id, heights, result, algo_version, commitment and
prev_commitment -- everything the static verify page needs to recompute against
the chain (it fetches the 144 block hashes from a public explorer client-side).

Run where the DB lives:  python scripts/export_static.py [--out site]
Publish with:            scripts/publish-pages.sh
"""
import argparse
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main():
    ap = argparse.ArgumentParser(description="Export the static GitHub Pages snapshot.")
    ap.add_argument("--out", default=os.path.join(REPO, "site"), help="output directory (default: site/)")
    args = ap.parse_args()

    from app.lotto import get_all_draws, get_commitment_head, ALGO_VERSION
    from verify import GENESIS_PREV

    draws = get_all_draws()
    records = []
    prev = GENESIS_PREV
    for d in draws:
        records.append({
            "id": d.id,
            "start_height": d.start_height,
            "end_height": d.end_height,
            "front": d.front_list,
            "back": d.back_list,
            "algo_version": getattr(d, "algo_version", ALGO_VERSION) or ALGO_VERSION,
            "commitment": d.commitment,
            "prev_commitment": prev,
            "timestamp": d.timestamp,
        })
        prev = d.commitment

    head = get_commitment_head()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump({"count": len(records), "head": head, "algo_version": ALGO_VERSION, "draws": records},
                  f, separators=(",", ":"))
    with open(os.path.join(args.out, "head.json"), "w") as f:
        json.dump(head, f)

    web = os.path.join(REPO, "web")
    for name in ("index.html", "verify.html", "stats.html"):
        shutil.copy(os.path.join(web, name), os.path.join(args.out, name))
    cname_src = os.path.join(web, "CNAME")
    if os.path.exists(cname_src):
        # Custom domain marker -- must ride along every publish or GitHub Pages
        # drops the domain on the next (force-pushed) republish.
        shutil.copy(cname_src, os.path.join(args.out, "CNAME"))
    for name in ("verify.js", "stats.js", "style.css"):
        shutil.copy(os.path.join(REPO, "static", name), os.path.join(args.out, name))

    kb = os.path.getsize(os.path.join(args.out, "index.json")) / 1024
    head_hex = (head.get("head") or "")[:16]
    print(f"Exported {len(records)} draws to {args.out}/ (index.json {kb:.0f} KB), head={head_hex}...")


if __name__ == "__main__":
    main()
