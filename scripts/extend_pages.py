#!/usr/bin/env python3
"""
Extend the published static snapshot with any newly-confirmed draws, straight
from the chain -- no database, no heavy deps. Reuses verify.py (stdlib).

Reads an existing site/index.json (which carries every draw's result + commitment
chain), finds the latest draw, and for each fully-confirmed 144-block window
beyond it fetches the hashes from mempool.space, recomputes the result + chains
the commitment, and appends it. Rewrites index.json + head.json in place.

Usage:  python scripts/extend_pages.py [site/index.json]
Env:    DRAW_CONFIRMATIONS (default 6), MAX_NEW_DRAWS (default 10)
"""
import datetime
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import verify  # stdlib-only

CONFIRMATIONS = int(os.environ.get("DRAW_CONFIRMATIONS", "6"))
MAX_NEW = int(os.environ.get("MAX_NEW_DRAWS", "10"))
ALGO = "v1"


def _chain_tip():
    return int(verify._http_get("https://mempool.space/api/blocks/tip/height").strip())


def _block_timestamp(block_hash):
    data = json.loads(verify._http_get(f"https://mempool.space/api/block/{block_hash}"))
    ts = datetime.datetime.fromtimestamp(int(data["timestamp"]), tz=datetime.timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "site", "index.json")
    with open(path) as f:
        data = json.load(f)
    draws = data["draws"]

    next_id = (draws[-1]["id"] + 1) if draws else 0
    prev_commitment = draws[-1]["commitment"] if draws else verify.GENESIS_PREV

    confirmed_tip = _chain_tip() - CONFIRMATIONS
    added = 0
    while added < MAX_NEW:
        start, end = verify.heights_for(next_id)
        if end > confirmed_tip:
            break  # window not fully confirmed yet
        hashes = verify.fetch_from_mempool(start, end)
        front, back, seed = verify.generate(hashes)
        commitment = verify.commitment_for(prev_commitment, next_id, ALGO, seed, front, back, start, end)
        draws.append({
            "id": next_id,
            "start_height": start,
            "end_height": end,
            "front": front,
            "back": back,
            "algo_version": ALGO,
            "commitment": commitment,
            "prev_commitment": prev_commitment,
            "timestamp": _block_timestamp(hashes[-1]),
        })
        prev_commitment = commitment
        next_id += 1
        added += 1

    head = ({"head": prev_commitment, "draw_id": draws[-1]["id"],
             "count": len(draws), "algo_version": ALGO} if draws
            else {"head": verify.GENESIS_PREV, "draw_id": -1, "count": 0, "algo_version": ALGO})

    if added:
        data["count"] = len(draws)
        data["head"] = head
        with open(path, "w") as f:
            json.dump(data, f, separators=(",", ":"))

    # Always (re)write head.json next to index.json, even on a no-op refresh: the
    # publish workflow only carries index.json forward, so without this head.json
    # would 404 after any refresh that adds 0 draws (the common case).
    with open(os.path.join(os.path.dirname(path), "head.json"), "w") as f:
        json.dump(head, f)

    print(f"ADDED={added} total={len(draws)} latest={(draws[-1]['id'] if draws else None)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
