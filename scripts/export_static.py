#!/usr/bin/env python3
"""
Export a static, self-verifying snapshot of the draw history into site/.

Reads the local SQLite draw cache with the stdlib `sqlite3` module only (no
app package, no SQLModel) and writes a small static site (no 144-hash blobs, so
the snapshot stays ~1-2MB):
  site/index.json  - {count, head, algo_version, draws:[slim records]}
  site/head.json   - the commitment head
  site/index.html, verify.html, stats.html, trend.html + verify.js, stats.js,
  trend.js, style.css, favicon.svg, CNAME, and anchors/

Each slim record has id, heights, result, algo_version, commitment and
prev_commitment -- everything the static verify page needs to recompute against
the chain (it fetches the 144 block hashes from a public explorer client-side).

The DB is just an optional local cache: the GitHub Actions cron
(scripts/extend_pages.py) maintains the published snapshot without any DB. This
script is for the initial build / local disaster-recovery from a DB you have.

Run where the DB lives:  python scripts/export_static.py [--out site] [--db data/database.db]
Publish with:            scripts/publish-pages.sh
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from verify import GENESIS_PREV, ALGO_VERSION  # noqa: E402  (stdlib-only auditor)
import artifacts  # noqa: E402  (local: downstream beacon artifacts)


def read_draws(db_path):
    """Read all draws from the SQLite cache, oldest first, via stdlib sqlite3.

    Returns slim records (id/heights/result/commitment/prev_commitment/...),
    chaining prev_commitment from GENESIS_PREV, plus the commitment head dict.
    """
    con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        rows = con.execute(
            "SELECT id, front, back, start_height, end_height, algo_version, "
            "commitment, timestamp FROM draw ORDER BY id ASC"
        ).fetchall()
    finally:
        con.close()

    records = []
    prev = GENESIS_PREV
    for (id_, front, back, start_h, end_h, algo, commitment, ts) in rows:
        records.append({
            "id": id_,
            "start_height": start_h,
            "end_height": end_h,
            "front": json.loads(front),
            "back": json.loads(back),
            "algo_version": algo or ALGO_VERSION,
            "commitment": commitment,
            "prev_commitment": prev,
            "timestamp": ts,
        })
        prev = commitment

    if records:
        last = records[-1]
        head = {"head": last["commitment"], "draw_id": last["id"],
                "count": len(records), "algo_version": ALGO_VERSION}
    else:
        head = {"head": GENESIS_PREV, "draw_id": -1, "count": 0, "algo_version": ALGO_VERSION}
    return records, head


def main():
    ap = argparse.ArgumentParser(description="Export the static GitHub Pages snapshot.")
    ap.add_argument("--out", default=os.path.join(REPO, "site"), help="output directory (default: site/)")
    ap.add_argument("--db", default=os.path.join(REPO, "data", "database.db"),
                    help="sqlite draw cache path (default: data/database.db)")
    args = ap.parse_args()

    records, head = read_draws(args.db)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump({"count": len(records), "head": head, "algo_version": ALGO_VERSION, "draws": records},
                  f, separators=(",", ":"))
    with open(os.path.join(args.out, "head.json"), "w") as f:
        json.dump(head, f)
    artifacts.write_beacon(args.out, records, head)  # latest.json + feed.json
    # Offline rebuild: no sources probed; the next cron run writes real health.
    artifacts.write_status(args.out, [], time.time(), head,
                           note="rebuilt offline from the local cache; sources not probed")

    web = os.path.join(REPO, "web")
    for name in ("index.html", "verify.html", "stats.html", "trend.html"):
        shutil.copy(os.path.join(web, name), os.path.join(args.out, name))
    cname_src = os.path.join(web, "CNAME")
    if os.path.exists(cname_src):
        # Custom domain marker -- must ride along every publish or GitHub Pages
        # drops the domain on the next (force-pushed) republish.
        shutil.copy(cname_src, os.path.join(args.out, "CNAME"))
    for name in ("verify.js", "stats.js", "trend.js", "style.css", "favicon.svg"):
        shutil.copy(os.path.join(REPO, "static", name), os.path.join(args.out, name))
    anchors_src = os.path.join(REPO, "anchors")
    if os.path.isdir(anchors_src):  # serve the OpenTimestamps head proofs at /anchors/
        shutil.copytree(anchors_src, os.path.join(args.out, "anchors"), dirs_exist_ok=True)

    kb = os.path.getsize(os.path.join(args.out, "index.json")) / 1024
    head_hex = (head.get("head") or "")[:16]
    print(f"Exported {len(records)} draws to {args.out}/ (index.json {kb:.0f} KB), head={head_hex}...")


if __name__ == "__main__":
    main()
