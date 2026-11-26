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
  # (node behind Cloudflare Access? also set the service token:)
  #   export CF_ACCESS_CLIENT_ID="....access"  CF_ACCESS_CLIENT_SECRET="..."
  python verify.py 6315 --source core

  # Offline check against the local database:
  python verify.py 6315 --source db --db data/database.db
"""
import argparse
import base64
import hashlib
import hmac
import http.client
import io
import json
import os
import sys
import threading
import time
import urllib.error
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


def head_for(draws):
    """The published `head` object for a draws list (oldest-first): the last draw's
    commitment + its id/count, or the genesis sentinel for an empty history. One
    definition so both publishers (extend_pages, export_static) agree byte-for-byte."""
    if not draws:
        return {"head": GENESIS_PREV, "draw_id": -1, "count": 0, "algo_version": ALGO_VERSION}
    last = draws[-1]
    return {"head": last["commitment"], "draw_id": last["id"],
            "count": len(draws), "algo_version": ALGO_VERSION}


def _normalize(h):
    h = h.strip().lower()
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"invalid block hash: {h!r}")
    return h


# ----------------------------- hash sources -----------------------------
_HTTP_POOL = threading.local()
_ORIG_URLOPEN = urllib.request.urlopen


def close_http_pool():
    """Close any persistent HTTP(S) connections cached on the current thread."""
    conns = getattr(_HTTP_POOL, "conns", None)
    if not conns:
        return
    for conn in list(conns.values()):
        try:
            conn.close()
        except OSError:
            pass
    conns.clear()


def _open_http_conn(scheme, host, port, timeout):
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    return cls(host, port, timeout=timeout)


_MAX_HTTP_BYTES = 32 * 1024 * 1024  # 32 MiB safety ceiling against unbounded HTTP bodies


def _parse_http_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise urllib.error.URLError(f"malformed URL {url!r}: {e}") from e
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise urllib.error.URLError(f"unsupported URL: {url!r}")
    return parts, (port or (443 if parts.scheme == "https" else 80))


def _send_pooled_once(method, url, data, headers, timeout, max_bytes=_MAX_HTTP_BYTES):
    """Send one HTTP(S) request over a thread-local Keep-Alive connection."""
    parts, port = _parse_http_url(url)
    origin = (parts.scheme, parts.hostname, port)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    req_headers = {"Connection": "keep-alive", **(headers or {})}

    conns = getattr(_HTTP_POOL, "conns", None)
    if conns is None:
        conns = {}
        _HTTP_POOL.conns = conns
    conn = conns.get(origin)
    reused = conn is not None and conn.sock is not None
    if conn is None:
        conn = _open_http_conn(*origin, timeout)
        conns[origin] = conn
    else:
        conn.timeout = timeout
        if conn.sock is not None:
            conn.sock.settimeout(timeout)

    def _roundtrip(c):
        c.request(method, target, body=data, headers=req_headers)
        r = c.getresponse()
        body = r.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise urllib.error.URLError(f"HTTP response exceeds {max_bytes} bytes: {url}")
        return r, body

    try:
        resp, raw = _roundtrip(conn)
    except (http.client.HTTPException, OSError):
        conns.pop(origin, None)
        try:
            conn.close()
        except OSError:
            pass
        if not reused:
            raise
        conn = _open_http_conn(*origin, timeout)
        conns[origin] = conn
        try:
            resp, raw = _roundtrip(conn)
        except (http.client.HTTPException, OSError):
            conns.pop(origin, None)
            try:
                conn.close()
            except OSError:
                pass
            raise

    if resp.will_close:
        conns.pop(origin, None)
        conn.close()
    return resp.status, resp.reason, resp.headers, raw, origin


def _exec_http(method, url, data, headers, timeout, validate_url=None,
               allow_redirects=True, max_bytes=_MAX_HTTP_BYTES):
    """Execute one HTTP exchange (following up to 5 redirects) using Keep-Alive,
    or fall back to urllib.request.urlopen when patched in unit tests."""
    if validate_url is not None:
        validate_url(url)
    if urllib.request.urlopen is not _ORIG_URLOPEN:
        req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise urllib.error.URLError(f"HTTP response exceeds {max_bytes} bytes: {url}")
        return body
    cur_url, cur_method, cur_data = url, method, data
    cur_headers = dict(headers or {})
    hops = 6 if allow_redirects else 1
    for _ in range(hops):
        status, reason, resp_headers, raw, origin = _send_pooled_once(
            cur_method, cur_url, cur_data, cur_headers, timeout, max_bytes=max_bytes
        )
        loc = resp_headers.get("Location") if (allow_redirects and resp_headers) else None
        if status in (301, 302, 303, 307, 308) and loc:
            nxt = urllib.parse.urljoin(cur_url, loc)
            if validate_url is not None:
                validate_url(nxt)
            np, nport = _parse_http_url(nxt)
            if origin[0] == "https" and np.scheme != "https":
                raise urllib.error.URLError(f"refused HTTPS->HTTP downgrade redirect: {nxt!r}")
            if (np.scheme, np.hostname, nport) != origin:
                cur_headers = {
                    k: v for k, v in cur_headers.items()
                    if k.lower() not in ("authorization", "cf-access-client-id", "cf-access-client-secret")
                }
            if status in (301, 302, 303) and cur_method != "GET":
                cur_method, cur_data = "GET", None
            cur_url = nxt
            continue
        if status >= 400:
            raise urllib.error.HTTPError(cur_url, status, reason, resp_headers, io.BytesIO(raw))
        return raw
    raise urllib.error.URLError(f"too many redirects: {url}")


def _retry_after(http_error):
    """Numeric Retry-After header (seconds) from an HTTPError, capped; None if absent/odd."""
    try:
        v = http_error.headers.get("Retry-After")
        return min(float(v), 10.0) if v else None
    except (TypeError, ValueError, AttributeError):
        return None


def _http_request_bytes(url, method="GET", data=None, headers=None, timeout=30, retries=3,
                        validate_url=None, allow_redirects=True, max_bytes=_MAX_HTTP_BYTES):
    """HTTP(S) request returning raw bytes with bounded retry/backoff and Keep-Alive."""
    for attempt in range(retries):
        try:
            return _exec_http(
                method, url, data, headers, timeout,
                validate_url=validate_url, allow_redirects=allow_redirects, max_bytes=max_bytes,
            )
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
            time.sleep(_retry_after(e) or 0.5 * (2 ** attempt))
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            if attempt == retries - 1:
                raise
            time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError("unreachable")


def _http_get(url, timeout=30, retries=3):
    """GET with bounded retry/backoff and persistent Keep-Alive pooling."""
    raw = _http_request_bytes(
        url, headers={"User-Agent": "lucky2049-verify/1.0"}, timeout=timeout, retries=retries
    )
    return raw.decode("utf-8")


class _BatchUnsupportedError(RuntimeError):
    """Raised when an RPC endpoint/proxy does not support JSON-RPC batch arrays."""


def _rpc_endpoint(rpc_url):
    """Parse Basic-Auth userinfo + optional Cloudflare Access headers for Core RPC."""
    parts = urllib.parse.urlsplit(rpc_url)
    headers = {"Content-Type": "text/plain", "User-Agent": "lucky2049-verify/1.0"}
    cf_id = os.environ.get("CF_ACCESS_CLIENT_ID")
    cf_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET")
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret
    url = rpc_url
    if parts.username is not None:
        token = base64.b64encode(f"{parts.username}:{parts.password or ''}".encode()).decode()
        headers["Authorization"] = "Basic " + token
        host_fmt = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else (parts.hostname or "")
        netloc = host_fmt + (f":{parts.port}" if parts.port else "")
        url = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url, headers


def _rpc_call(rpc_url, method, params, timeout=30, retries=3):
    """One Bitcoin Core JSON-RPC POST with Basic-Auth, CF-Access, Keep-Alive, and retry."""
    url, headers = _rpc_endpoint(rpc_url)
    payload = json.dumps({"jsonrpc": "1.0", "id": "lucky2049", "method": method, "params": params}).encode()
    raw = _http_request_bytes(url, method="POST", data=payload, headers=headers, timeout=timeout, retries=retries)
    res = json.loads(raw.decode("utf-8"))
    if res.get("error"):
        raise RuntimeError(f"RPC error: {res['error']}")
    return res["result"]


def _rpc_batch_call(rpc_url, calls, timeout=30, retries=3):
    """Batch multiple JSON-RPC calls into a single HTTP POST and reassemble by id."""
    if not calls:
        return []
    url, headers = _rpc_endpoint(rpc_url)
    payload = json.dumps([
        {"jsonrpc": "1.0", "id": req_id, "method": method, "params": params}
        for req_id, method, params in calls
    ]).encode()
    try:
        raw = _http_request_bytes(url, method="POST", data=payload, headers=headers, timeout=timeout, retries=retries)
    except urllib.error.HTTPError as e:
        if e.code in (400, 404, 405, 501):
            raise _BatchUnsupportedError(f"HTTP {e.code}") from e
        raise
    try:
        res = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise _BatchUnsupportedError("non-JSON batch response") from e
    if not isinstance(res, list) or len(res) != len(calls):
        raise _BatchUnsupportedError("endpoint did not return a matching JSON-RPC batch list")
    by_id = {}
    for item in res:
        if not isinstance(item, dict):
            raise _BatchUnsupportedError("non-object item in JSON-RPC batch")
        if item.get("error"):
            raise RuntimeError(f"RPC error: {item['error']}")
        by_id[item.get("id")] = item.get("result")
    expected_ids = [req_id for req_id, _, _ in calls]
    if any(req_id not in by_id for req_id in expected_ids):
        raise _BatchUnsupportedError("missing request id in JSON-RPC batch response")
    return [by_id[req_id] for req_id in expected_ids]


def _core_rpc_url_from_env():
    rpc_url = os.environ.get("BITCOIN_RPC_URL")
    if rpc_url:
        return rpc_url
    user = os.environ.get("BITCOIN_RPC_USER", "")
    pw = os.environ.get("BITCOIN_RPC_PASSWORD", "")
    host = os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")
    port = os.environ.get("BITCOIN_RPC_PORT", "8332")
    if not user:
        raise RuntimeError("Set BITCOIN_RPC_URL or BITCOIN_RPC_USER/PASSWORD/HOST/PORT")
    return f"http://{user}:{pw}@{host}:{port}"


def _fetch_core_hashes(rpc_url, start, end):
    """Fetch [start..end] hashes via JSON-RPC batch POST, falling back to single calls
    only if an intermediary proxy rejects JSON-RPC batch arrays."""
    calls = [(h, "getblockhash", [h]) for h in range(start, end + 1)]
    try:
        results = _rpc_batch_call(rpc_url, calls)
        return [_normalize(h) for h in results]
    except _BatchUnsupportedError:
        return [_normalize(_rpc_call(rpc_url, "getblockhash", [h])) for h in range(start, end + 1)]


def fetch_from_core(start, end):
    """Bitcoin Core JSON-RPC: getblockhash(height). Source of truth."""
    return _fetch_core_hashes(_core_rpc_url_from_env(), start, end)


def _fetch_esplora_pages(base_url, start, end):
    """Fast path for Esplora APIs: walk descending /blocks/{height} pages (10-15 blocks/call)."""
    slots = [None] * (end - start + 1)
    cursor = end
    while cursor >= start:
        items = json.loads(_http_get(f"{base_url}/blocks/{cursor}"))
        if not isinstance(items, list) or not items:
            raise ValueError("empty Esplora blocks page")
        min_seen = None
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("invalid Esplora block entry")
            h, bh = item.get("height"), item.get("id")
            if type(h) is not int or not isinstance(bh, str):
                raise ValueError("invalid Esplora height/id")
            norm = _normalize(bh)
            if start <= h <= end:
                if slots[h - start] is not None and slots[h - start] != norm:
                    raise ValueError(f"conflicting hash at height {h}")
                slots[h - start] = norm
            min_seen = h if min_seen is None else min(min_seen, h)
        low = max(start, min_seen)
        if min_seen > cursor or any(slots[h - start] is None for h in range(low, cursor + 1)):
            raise ValueError(f"incomplete Esplora blocks page at {cursor}")
        cursor = min_seen - 1
        if cursor >= start:
            time.sleep(0.02)
    return slots


def _fetch_esplora_window(base_url, start, end):
    """Fetch [start..end] block hashes from an Esplora API via paged /blocks/{height},
    falling back to per-height /block-height/{h} if paging is unsupported or gapped."""
    if end < start:
        return []
    try:
        return _fetch_esplora_pages(base_url, start, end)
    except (ValueError, KeyError, urllib.error.URLError, http.client.HTTPException, OSError):
        out = []
        for h in range(start, end + 1):
            out.append(_normalize(_http_get(f"{base_url}/block-height/{h}")))
            time.sleep(0.05)
        return out


def fetch_from_mempool(start, end):
    """mempool.space Esplora API: paged /api/blocks/{h} with /api/block-height/{h} fallback."""
    return _fetch_esplora_window("https://mempool.space/api", start, end)


def fetch_from_blockstream(start, end):
    """blockstream.info Esplora API: paged /api/blocks/{h} with /api/block-height/{h} fallback."""
    return _fetch_esplora_window("https://blockstream.info/api", start, end)


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
_SITE_CACHE_TLS = threading.local()


def _is_commitment(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _is_int(value):
    return type(value) is int


def verify_chain(index):
    """Full linkage scan over a published static index ({draws:[...], head:{...}}).
    Mirrors static/verify.js verifyChain(): every draw 0..n-1 must be contiguous,
    carry valid 64-hex commitment + prev_commitment, link to its predecessor (or
    GENESIS_PREV for draw 0), and match index.head."""
    if not isinstance(index, dict) or not isinstance(index.get("draws"), list):
        return {"ok": False, "broken_at": "draws"}
    draws = index["draws"]
    if index.get("count") is not None and (not _is_int(index.get("count")) or index["count"] != len(draws)):
        return {"ok": False, "broken_at": "count"}
    for i, d in enumerate(draws):
        if (not isinstance(d, dict) or not _is_int(d.get("id")) or d["id"] != i
                or not _is_commitment(d.get("commitment"))
                or not _is_commitment(d.get("prev_commitment"))):
            return {"ok": False, "broken_at": i}
        expected = GENESIS_PREV if i == 0 else draws[i - 1]["commitment"]
        if d["prev_commitment"] != expected:
            return {"ok": False, "broken_at": i}
    head = index.get("head")
    if draws:
        if not isinstance(head, dict):
            return {"ok": False, "broken_at": "head"}
        last = draws[-1]
        if (head.get("head") != last["commitment"]
                or (head.get("draw_id") is not None and (not _is_int(head.get("draw_id")) or head["draw_id"] != last["id"]))
                or (head.get("count") is not None and (not _is_int(head.get("count")) or head["count"] != len(draws)))):
            return {"ok": False, "broken_at": "head"}
    return {"ok": True, "broken_at": None}


def _fetch_published(site, draw_id):
    """Fetch draw_id's published record from a site, normalized to one shape.

    Tries the live server's manifest API first; if it is unavailable (the
    public deployment is the static GitHub Pages snapshot, which has no API),
    falls back to the static index.json. Within a verify.main() run, the parsed
    static snapshot + its full-chain scan are memoized per site so resolving N
    and N-1 downloads index.json only once.
    """
    base = site.rstrip("/")
    run_cache = getattr(_SITE_CACHE_TLS, "sites", None)
    cached = run_cache.get(base) if run_cache is not None else None
    if cached is None or cached.get("mode") != "static":
        try:
            m = json.loads(_http_get(f"{base}/api/draw/{draw_id}/manifest"))
            if run_cache is not None:
                run_cache[base] = {"mode": "api"}
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

    if cached is not None and cached.get("mode") == "static":
        idx, snap_chain = cached["index"], cached["chain"]
    else:
        idx = json.loads(_http_get(f"{base}/index.json"))
        snap_chain = verify_chain(idx)
        if run_cache is not None:
            run_cache[base] = {"mode": "static", "index": idx, "chain": snap_chain}

    rec = next((d for d in idx.get("draws", []) if isinstance(d, dict) and d.get("id") == draw_id), None)
    if rec is None:
        raise LookupError(f"draw {draw_id} not found in {base}/index.json")
    return {
        "front": rec.get("front"), "back": rec.get("back"),
        "block_hashes": [],  # snapshot omits hashes by design; the chain is truth
        "commitment": rec.get("commitment"),
        "prev_commitment": rec.get("prev_commitment"),
        "algo_version": rec.get("algo_version", idx.get("algo_version", ALGO_VERSION)),
        "kind": "static",
        "snapshot_chain": snap_chain,
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
        _SITE_CACHE_TLS.sites = {}
        try:
            try:
                pub = _fetch_published(args.site, args.draw_id)
            except Exception as e:
                print(f"ERROR: could not fetch published result: {e}", file=sys.stderr)
                return 2
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

            # Tamper-evidence: recompute this draw's commitment from the PREVIOUS
            # DRAW'S commitment -- resolved from draw N-1's own record, never from this
            # record's self-declared prev_commitment field -- AND verify the entire
            # published static snapshot chain (0..count-1 + head) when available.
            published_commitment = pub["commitment"]
            if not _is_commitment(published_commitment):
                print("ERROR: published commitment is missing or invalid", file=sys.stderr)
                return 2
            if args.draw_id == 0:
                prev = GENESIS_PREV
            else:
                try:
                    prev = _fetch_published(args.site, args.draw_id - 1)["commitment"]
                except Exception as e:
                    print(f"ERROR: could not fetch previous commitment: {e}", file=sys.stderr)
                    return 2
            if not _is_commitment(prev):
                print("ERROR: previous commitment is missing or invalid", file=sys.stderr)
                return 2

            declared = pub.get("prev_commitment")
            if pub["kind"] == "static" and not _is_commitment(declared):
                print("ERROR: record's prev_commitment is missing or invalid", file=sys.stderr)
                return 2
            recomputed = commitment_for(prev, args.draw_id, pub["algo_version"], seed, front, back, start, end)
            chain_ok = (recomputed == published_commitment)
            snap_chain = pub.get("snapshot_chain")
            snap_ok = (snap_chain is None or snap_chain.get("ok", False))
            decl_ok = (declared is None or declared == prev)
            if chain_ok and decl_ok and snap_ok:
                suffix = ", full chain intact" if snap_chain is not None else ""
                print(f"CHAIN MATCH : PASS (links to draw {args.draw_id - 1}{suffix})")
            else:
                exit_code = 1
                if not chain_ok:
                    print(f"CHAIN MATCH : FAIL (links to draw {args.draw_id - 1})")
                if not decl_ok:
                    print("CHAIN MATCH : FAIL (record's prev_commitment != draw "
                          f"{args.draw_id - 1}'s commitment -- chain tampered)")
                if not snap_ok:
                    print("CHAIN MATCH : FAIL (full snapshot chain broken at "
                          f"{snap_chain.get('broken_at')} -- chain tampered)")
        finally:
            _SITE_CACHE_TLS.sites = None

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
