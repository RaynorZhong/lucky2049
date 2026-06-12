#!/usr/bin/env python3
"""
Write a dated anchor file for the current published commitment head, so it can
be timestamped with OpenTimestamps (see .github/workflows/anchor-head.yml).

The head commits to the entire draw history via the hash chain. Stamping it with
OpenTimestamps pins it to the Bitcoin blockchain at a point in time, so the
operator cannot later rewrite history and recompute a consistent head -- the old
head is provably older than any rewrite. This closes the last gap in the
tamper-evidence story (the code provides the chain; the external, trustless
timestamp is the anchor).

Reads the published snapshot's head (from index.json, which is always present
and authoritative) and writes anchors/<draw_id>.head.json, unless that draw is
already anchored. The companion .ots proof is produced by `ots stamp` in the
workflow. Stdlib only.

Usage:  python scripts/anchor_head.py [site-base-url-or-local-dir]
        # default base: https://lucky2049.com   (local: ./site)
"""
import json
import os
import sys
import tempfile
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = sys.argv[1] if len(sys.argv) > 1 else "https://lucky2049.com"


def load_head(base):
    """Return the head dict from {base}/index.json (URL or local directory)."""
    if base.startswith(("http://", "https://")):
        url = base.rstrip("/") + "/index.json"
        req = urllib.request.Request(url, headers={"User-Agent": "lucky2049-anchor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    else:
        with open(os.path.join(base, "index.json")) as f:
            data = json.load(f)
    return data.get("head") or {}


def main(base=None, anchors_dir=None):
    base = BASE if base is None else base
    anchors_dir = os.path.join(REPO, "anchors") if anchors_dir is None else anchors_dir
    head = load_head(base)
    draw_id = head.get("draw_id")
    head_hex = head.get("head")
    if draw_id is None or draw_id < 0 or not head_hex:
        print(f"no head to anchor (head = {head})")
        return 0

    os.makedirs(anchors_dir, exist_ok=True)
    path = os.path.join(anchors_dir, f"{draw_id:06d}.head.json")
    if os.path.exists(path):
        print(f"draw {draw_id} already anchored: {os.path.basename(path)}")
        return 0

    record = {
        "head": head_hex,
        "draw_id": draw_id,
        "count": head.get("count"),
        "algo_version": head.get("algo_version", "v1"),
    }
    # Atomic write (temp + replace): a crash can't leave a truncated anchor file.
    # Keep the exact format (indent=2, sorted, trailing newline) the existing OTS
    # proofs were stamped against.
    fd, tmp = tempfile.mkstemp(dir=anchors_dir, prefix=".tmp-", suffix=".head.json")
    with os.fdopen(fd, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    print(f"wrote {os.path.basename(path)} (head {head_hex[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
