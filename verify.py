#!/usr/bin/env python3
"""
verify.py — Independent verifier for lucky2049 draws (algorithm spec v1).

Given a draw_id, this script:
  1. Fetches the 144 Bitcoin mainnet block hashes for heights
     [draw_id*144, draw_id*144+143] from an INDEPENDENT source
     (your own full node, a public explorer, or the local DB),
  2. Recomputes the draw result exactly per SPEC.md (algorithm v1),
  3. Optionally compares against the result published by a running site
     and cross-checks that the site's stored hashes match the chain.

Standard library only — no third-party packages required.

Examples
--------
  # Recompute draw 6315 using a public explorer, compare to a published result:
  python verify.py 6315 --source mempool --site http://www.lucky2049.com:8000

  # Use your own Bitcoin Core node as the source of truth:
  export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
  python verify.py 6315 --source core

  # Offline check against the local database:
  python verify.py 6315 --source db --db db/database.db
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

# ---- Algorithm constants (FROZEN, must match SPEC.md / lotto.py v1) ----
ALGO_VERSION = "v1"
NUM_BLOCKCHAIN = 144
BLUE_BALL_MAX = 35
RED_BALL_MAX = 12
BLUE_BALL_NUM = 5
RED_BALL_NUM = 2


# ----------------------------- core algorithm -----------------------------
def generate(hashes):
    """Reproduce (front, back) from 144 lowercase-hex block hashes (ascending height)."""
    if len(hashes) != NUM_BLOCKCHAIN:
        raise ValueError(f"expected {NUM_BLOCKCHAIN} hashes, got {len(hashes)}")
    seed = hashlib.sha256("".join(hashes).encode("utf-8")).digest()
    nums = [
        int.from_bytes(hmac.new(seed, str(k).encode("utf-8"), hashlib.sha256).digest(), "big")
        for k in range(BLUE_BALL_NUM + RED_BALL_NUM)
    ]
    front_pool = list(range(1, BLUE_BALL_MAX + 1))
    front = sorted(front_pool.pop(nums[i] % len(front_pool)) for i in range(BLUE_BALL_NUM))
    back_pool = list(range(1, RED_BALL_MAX + 1))
    back = sorted(back_pool.pop(nums[BLUE_BALL_NUM + i] % len(back_pool)) for i in range(RED_BALL_NUM))
    return front, back, seed.hex()


def heights_for(draw_id):
    start = draw_id * NUM_BLOCKCHAIN
    return start, start + NUM_BLOCKCHAIN - 1


def _normalize(h):
    h = h.strip().lower()
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"invalid block hash: {h!r}")
    return h


# ----------------------------- hash sources -----------------------------
def _http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "lucky2049-verify/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def fetch_from_core(start, end):
    """Bitcoin Core JSON-RPC: getblockhash(height). Source of truth."""
    rpc_url = os.environ.get("BITCOIN_RPC_URL")
    if not rpc_url:
        user = os.environ.get("BITCOIN_RPC_USER", "")
        pw = os.environ.get("BITCOIN_RPC_PASSWORD", "")
        host = os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")
        port = os.environ.get("BITCOIN_RPC_PORT", "8332")
        if not user:
            raise RuntimeError("Set BITCOIN_RPC_URL or BITCOIN_RPC_USER/PASSWORD/HOST/PORT")
        rpc_url = f"http://{user}:{pw}@{host}:{port}"

    def rpc(method, params):
        payload = json.dumps({"jsonrpc": "1.0", "id": "verify", "method": method, "params": params})
        req = urllib.request.Request(
            rpc_url, data=payload.encode(), headers={"Content-Type": "text/plain"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
        if res.get("error"):
            raise RuntimeError(f"RPC error: {res['error']}")
        return res["result"]

    return [_normalize(rpc("getblockhash", [h])) for h in range(start, end + 1)]


def fetch_from_mempool(start, end):
    """mempool.space: GET /api/block-height/{h} -> block hash (text)."""
    out = []
    for h in range(start, end + 1):
        out.append(_normalize(_http_get(f"https://mempool.space/api/block-height/{h}")))
        time.sleep(0.05)
    return out


def fetch_from_blockstream(start, end):
    """blockstream.info: GET /api/block-height/{h} -> block hash (text)."""
    out = []
    for h in range(start, end + 1):
        out.append(_normalize(_http_get(f"https://blockstream.info/api/block-height/{h}")))
        time.sleep(0.05)
    return out


def fetch_from_db(start, end, db_path):
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    rows = conn.execute(
        "SELECT hash FROM bitcoin WHERE height BETWEEN ? AND ? ORDER BY height ASC",
        (start, end),
    ).fetchall()
    conn.close()
    return [_normalize(h) for (h,) in rows]


SOURCES = {
    "core": fetch_from_core,
    "mempool": fetch_from_mempool,
    "blockstream": fetch_from_blockstream,
    "db": fetch_from_db,
}


# ----------------------------- main -----------------------------
def main():
    ap = argparse.ArgumentParser(description="Independent verifier for lucky2049 draws (spec v1).")
    ap.add_argument("draw_id", type=int, help="draw id (N), uses heights N*144 .. N*144+143")
    ap.add_argument(
        "--source",
        choices=list(SOURCES),
        default=("core" if (os.environ.get("BITCOIN_RPC_URL") or os.environ.get("BITCOIN_RPC_USER")) else "mempool"),
        help="block-hash source (default: core if RPC env is set, else mempool)",
    )
    ap.add_argument("--db", default="db/database.db", help="sqlite path for --source db")
    ap.add_argument("--site", help="base URL of a running site to fetch the published result, e.g. http://host:8000")
    args = ap.parse_args()

    start, end = heights_for(args.draw_id)
    print(f"draw_id     : {args.draw_id}")
    print(f"algo        : {ALGO_VERSION} (Super Lotto {BLUE_BALL_NUM}/{BLUE_BALL_MAX} + {RED_BALL_NUM}/{RED_BALL_MAX})")
    print(f"heights     : {start} .. {end}  ({NUM_BLOCKCHAIN} blocks)")
    print(f"source      : {args.source}")

    fetch = SOURCES[args.source]
    hashes = fetch(start, end, args.db) if args.source == "db" else fetch(start, end)
    if len(hashes) != NUM_BLOCKCHAIN:
        print(f"ERROR: got {len(hashes)} hashes, need {NUM_BLOCKCHAIN}", file=sys.stderr)
        return 2

    front, back, seed = generate(hashes)
    print(f"first hash  : {hashes[0]}")
    print(f"last hash   : {hashes[-1]}")
    print(f"seed sha256 : {seed}")
    print(f"RECOMPUTED  : front {front}  back {back}")

    exit_code = 0
    if args.site:
        url = f"{args.site.rstrip('/')}/api/draw/{args.draw_id}/manifest"
        try:
            manifest = json.loads(_http_get(url))
        except Exception as e:
            print(f"WARN: could not fetch published manifest: {e}", file=sys.stderr)
            return exit_code
        pub = manifest.get("result", {})
        pf, pb = pub.get("front"), pub.get("back")
        print(f"PUBLISHED   : front {pf}  back {pb}")
        result_ok = (pf == front and pb == back)
        print(f"RESULT MATCH: {'PASS' if result_ok else 'FAIL'}")

        # Cross-check: do the site's stored hashes match the independent source?
        site_hashes = [_normalize(h) for h in manifest.get("block_hashes", [])]
        if site_hashes:
            hashes_ok = (site_hashes == hashes)
            print(f"HASHES MATCH: {'PASS' if hashes_ok else 'FAIL'} (site DB vs {args.source})")
            if not (result_ok and hashes_ok):
                exit_code = 1
        elif not result_ok:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
