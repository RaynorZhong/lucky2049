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
  # Recompute draw 6315 from a public explorer and compare to a published site
  # (works against the static GitHub Pages snapshot or a live server):
  python verify.py 6315 --source mempool --site https://lucky2049.com

  # Use your own Bitcoin Core node as the source of truth:
  export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
  python verify.py 6315 --source core

  # Offline check against the local database:
  python verify.py 6315 --source db --db data/database.db
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# ---- Algorithm constants (FROZEN, must match SPEC.md / static/verify.js v1) ----
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


# --------------------------- tamper-evidence chain ---------------------------
# Each draw is linked into a hash chain so the whole published history collapses
# into one 32-byte "head". Editing any past draw changes the head, which an
# auditor detects by recomputing the chain. Anchoring the head externally
# (OpenTimestamps / a git tag / a public post) is what stops the operator from
# rewriting history and recomputing a new consistent head.
GENESIS_PREV = "0" * 64  # prev-commitment of the very first draw (id 0)


def commitment_for(prev_hex, draw_id, algo_version, seed_hex, front, back, start_height, end_height):
    """Deterministic per-draw commitment. Binds this draw's published result and
    its exact block range (via the seed) to the entire prior history (via prev)."""
    payload = "|".join([
        prev_hex,
        str(draw_id),
        str(algo_version),
        seed_hex,
        ",".join(str(x) for x in front),
        ",".join(str(x) for x in back),
        str(start_height),
        str(end_height),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _rpc_call(rpc_url, method, params, timeout=30):
    """One Bitcoin Core JSON-RPC POST.

    Sends an explicit HTTP Basic-Auth header parsed from the URL's `user:pass@`:
    urllib does NOT authenticate from URL userinfo on its own, and would even try
    to resolve `user:pass@host` as a hostname -- so we strip the userinfo and add
    the Authorization header ourselves, then connect to the clean host."""
    parts = urllib.parse.urlsplit(rpc_url)
    headers = {"Content-Type": "text/plain"}
    url = rpc_url
    if parts.username is not None:
        token = base64.b64encode(f"{parts.username}:{parts.password or ''}".encode()).decode()
        headers["Authorization"] = "Basic " + token
        netloc = parts.hostname + (f":{parts.port}" if parts.port else "")
        url = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    payload = json.dumps({"jsonrpc": "1.0", "id": "lucky2049", "method": method, "params": params})
    req = urllib.request.Request(url, data=payload.encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read().decode())
    if res.get("error"):
        raise RuntimeError(f"RPC error: {res['error']}")
    return res["result"]


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
    return [_normalize(_rpc_call(rpc_url, "getblockhash", [h])) for h in range(start, end + 1)]


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


# ----------------------------- published site -----------------------------
def _fetch_published(site, draw_id):
    """Fetch draw_id's published record from a site, normalized to one shape.

    Tries the live server's manifest API first; if it is unavailable (the
    public deployment is the static GitHub Pages snapshot, which has no API),
    falls back to the static index.json. The slim static record inlines
    prev_commitment, so the chain check needs no extra request there; the live
    API does not, so prev_commitment is left None for the caller to resolve.

    Returns {front, back, block_hashes, commitment, prev_commitment,
    algo_version, kind} or raises if neither source is reachable.
    """
    base = site.rstrip("/")
    try:
        m = json.loads(_http_get(f"{base}/api/draw/{draw_id}/manifest"))
        res = m.get("result", {})
        return {
            "front": res.get("front"), "back": res.get("back"),
            "block_hashes": [_normalize(h) for h in m.get("block_hashes", [])],
            "commitment": m.get("commitment"),
            "prev_commitment": None,  # live API: previous draw fetched separately
            "algo_version": m.get("algo_version", ALGO_VERSION),
            "kind": "api",
        }
    except Exception:
        pass  # no live API -- fall back to the static snapshot
    idx = json.loads(_http_get(f"{base}/index.json"))
    rec = next((d for d in idx.get("draws", []) if d.get("id") == draw_id), None)
    if rec is None:
        raise LookupError(f"draw {draw_id} not found in {base}/index.json")
    return {
        "front": rec.get("front"), "back": rec.get("back"),
        "block_hashes": [],  # snapshot omits hashes by design; the chain is truth
        "commitment": rec.get("commitment"),
        "prev_commitment": rec.get("prev_commitment"),
        "algo_version": rec.get("algo_version", idx.get("algo_version", ALGO_VERSION)),
        "kind": "static",
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
    ap.add_argument("--db", default="data/database.db", help="sqlite path for --source db")
    ap.add_argument("--site", help="base URL of a published site to compare against -- a live server or the static GitHub Pages snapshot, e.g. https://lucky2049.com")
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
        try:
            pub = _fetch_published(args.site, args.draw_id)
        except Exception as e:
            print(f"WARN: could not fetch published result: {e}", file=sys.stderr)
            return exit_code
        pf, pb = pub["front"], pub["back"]
        print(f"PUBLISHED   : front {pf}  back {pb}  (via {pub['kind']})")
        result_ok = (pf == front and pb == back)
        print(f"RESULT MATCH: {'PASS' if result_ok else 'FAIL'}")
        if not result_ok:
            exit_code = 1

        # Cross-check the site's stored hashes against the independent source,
        # when exposed (live API only; the static snapshot omits them on purpose).
        if pub["block_hashes"]:
            hashes_ok = (pub["block_hashes"] == hashes)
            print(f"HASHES MATCH: {'PASS' if hashes_ok else 'FAIL'} (site DB vs {args.source})")
            if not hashes_ok:
                exit_code = 1

        # Tamper-evidence: recompute this draw's commitment from the previous
        # draw's commitment and check it matches what the site published.
        published_commitment = pub["commitment"]
        if published_commitment:
            algo = pub["algo_version"]
            prev = pub["prev_commitment"]
            if prev is None:  # live API record carries no prev -- resolve it
                if args.draw_id == 0:
                    prev = GENESIS_PREV
                else:
                    try:
                        prev = _fetch_published(args.site, args.draw_id - 1)["commitment"]
                    except Exception as e:
                        print(f"WARN: could not fetch previous commitment: {e}", file=sys.stderr)
            if prev:
                recomputed = commitment_for(prev, args.draw_id, algo, seed, front, back, start, end)
                chain_ok = (recomputed == published_commitment)
                print(f"CHAIN MATCH : {'PASS' if chain_ok else 'FAIL'} (links to draw {args.draw_id - 1})")
                if not chain_ok:
                    exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
