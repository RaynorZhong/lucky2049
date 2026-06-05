#!/usr/bin/env python3
"""
Extend the published static snapshot with any newly-confirmed draws, straight
from the chain -- no database, no heavy deps. Reuses verify.py (stdlib).

Reads an existing site/index.json (which carries every draw's result + commitment
chain), finds the latest draw, and for each fully-confirmed 144-block window
beyond it fetches the hashes, recomputes the result + chains the commitment, and
appends it. Rewrites index.json + head.json in place.

Tip / block hashes / timestamp come from an ordered list of sources with
automatic fallback, so a single explorer being down, rate-limiting, or otherwise
flaky can't stall publishing:
  1. a self-hosted Bitcoin Core node (preferred) when BITCOIN_RPC_URL or
     BITCOIN_RPC_USER/PASSWORD/HOST/PORT is set,
  2. mempool.space,
  3. blockstream.info.

Usage:  python scripts/extend_pages.py [site/index.json]
Env:    DRAW_CONFIRMATIONS (default 6), MAX_NEW_DRAWS (default 10),
        BITCOIN_RPC_URL or BITCOIN_RPC_USER/PASSWORD/HOST/PORT (optional Core source)
"""
import datetime
import json
import os
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import verify  # stdlib-only

CONFIRMATIONS = int(os.environ.get("DRAW_CONFIRMATIONS", "6"))
MAX_NEW = int(os.environ.get("MAX_NEW_DRAWS", "10"))
ALGO = "v1"

# Public explorers, tried in order. Both speak the same Esplora-style API:
#   GET /blocks/tip/height -> tip height (text)
#   GET /block-height/{h}  -> block hash (text)   [via verify.fetch_from_*]
#   GET /block/{hash}      -> {... "timestamp": <unix> ...}
EXPLORERS = [
    ("mempool", "https://mempool.space/api", verify.fetch_from_mempool),
    ("blockstream", "https://blockstream.info/api", verify.fetch_from_blockstream),
]


def _fmt_ts(unix):
    ts = datetime.datetime.fromtimestamp(int(unix), tz=datetime.timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


def _explorer_provider(name, base, fetch_hashes):
    """A source backed by a public Esplora explorer. Reuses verify's polite,
    rate-limited hash fetch; tip + timestamp ride the same base URL."""
    return {
        "name": name,
        "tip": lambda: int(verify._http_get(f"{base}/blocks/tip/height").strip()),
        "hashes": fetch_hashes,
        "timestamp": lambda block_hash: _fmt_ts(
            json.loads(verify._http_get(f"{base}/block/{block_hash}"))["timestamp"]
        ),
    }


def _core_rpc_url():
    """Bitcoin Core JSON-RPC URL from env, or None when not configured."""
    url = os.environ.get("BITCOIN_RPC_URL")
    if url:
        return url
    user = os.environ.get("BITCOIN_RPC_USER", "")
    if not user:
        return None
    pw = os.environ.get("BITCOIN_RPC_PASSWORD", "")
    host = os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")
    port = os.environ.get("BITCOIN_RPC_PORT", "8332")
    return f"http://{user}:{pw}@{host}:{port}"


def _core_provider():
    """Preferred source when a self-hosted node is configured; else None."""
    rpc_url = _core_rpc_url()
    if not rpc_url:
        return None

    def rpc(method, params):
        payload = json.dumps({"jsonrpc": "1.0", "id": "extend", "method": method, "params": params})
        req = urllib.request.Request(rpc_url, data=payload.encode(), headers={"Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
        if res.get("error"):
            raise RuntimeError(f"RPC error: {res['error']}")
        return res["result"]

    return {
        "name": "core",
        "tip": lambda: int(rpc("getblockcount", [])),
        "hashes": lambda start, end: [verify._normalize(rpc("getblockhash", [h])) for h in range(start, end + 1)],
        "timestamp": lambda block_hash: _fmt_ts(rpc("getblock", [block_hash])["time"]),
    }


def _providers():
    """Ordered sources: self-hosted Core first (if configured), then explorers."""
    provs = []
    core = _core_provider()
    if core:
        provs.append(core)
    provs += [_explorer_provider(n, b, f) for n, b, f in EXPLORERS]
    return provs


def _call(providers, op, *args):
    """Run `op` on each provider in turn; return the first success. If every
    source fails, raise with all of their errors so the cron log says why."""
    errors = []
    for p in providers:
        try:
            return p[op](*args)
        except Exception as e:  # network / HTTP / parse / RPC -- try the next source
            errors.append(f"{p['name']}: {e}")
    raise RuntimeError(f"all sources failed for '{op}' -> " + " | ".join(errors))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "site", "index.json")
    with open(path) as f:
        data = json.load(f)
    draws = data["draws"]

    next_id = (draws[-1]["id"] + 1) if draws else 0
    prev_commitment = draws[-1]["commitment"] if draws else verify.GENESIS_PREV

    providers = _providers()
    confirmed_tip = _call(providers, "tip") - CONFIRMATIONS
    added = 0
    while added < MAX_NEW:
        start, end = verify.heights_for(next_id)
        if end > confirmed_tip:
            break  # window not fully confirmed yet
        hashes = _call(providers, "hashes", start, end)
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
            "timestamp": _call(providers, "timestamp", hashes[-1]),
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
