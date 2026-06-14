#!/usr/bin/env python3
"""
Extend the published static snapshot with any newly-confirmed draws, straight
from the chain -- no database, no heavy deps. Reuses verify.py (stdlib).

Reads an existing site/index.json (which carries every draw's result + commitment
chain), finds the latest draw, and for each fully-confirmed 144-block window
beyond it fetches the hashes, recomputes the result + chains the commitment, and
appends it. Rewrites index.json + head.json in place.

Sources, in order: a self-hosted Bitcoin Core node (when BITCOIN_RPC_* is set),
then mempool.space, then blockstream.info.
  - The 144 block hashes are the inputs baked into the *irreversible* commitment
    chain, so they must AGREE across >= MIN_SOURCE_AGREEMENT (default 2)
    independent sources before a draw is committed. On disagreement -- or too few
    reachable sources -- that draw is HELD (not published) and retried next run,
    so one bad / forked / compromised explorer can never corrupt history.
  - Tip and timestamp are not part of the commitment, so they use plain
    first-success fallback (a timestamp outage falls back to null, never sinking
    an otherwise-good draw).
  - Defense-in-depth: an idle run re-verifies the most recent published draw
    against the live chain (reorg self-audit); a result mismatch, or a quorum tip
    that regressed below the last committed window, is recorded in status.json
    `note` and ALARMED by the workflow -- never silently rewritten.

Usage:  python scripts/extend_pages.py [site/index.json]
Env:    DRAW_CONFIRMATIONS (default 6), MAX_NEW_DRAWS (default 10),
        MIN_SOURCE_AGREEMENT (default 2),
        BITCOIN_RPC_URL or BITCOIN_RPC_USER/PASSWORD/HOST/PORT (optional Core source),
        CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET (optional Cloudflare Access
        service token, when the Core endpoint sits behind Zero Trust)
"""
import datetime
import hashlib
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import verify  # stdlib-only
import artifacts  # local: downstream beacon artifacts (latest.json / feed.json)

CONFIRMATIONS = int(os.environ.get("DRAW_CONFIRMATIONS", "6"))
MAX_NEW = int(os.environ.get("MAX_NEW_DRAWS", "10"))
MIN_AGREEMENT = int(os.environ.get("MIN_SOURCE_AGREEMENT", "2"))  # sources that must agree on a window's hashes
# Re-verify the latest published draw against the live chain while it's still within
# this many blocks of the tip -- a reorg horizon far beyond CONFIRMATIONS (the commit
# gate) yet beyond anything mainnet has ever seen, so the audit cost stays bounded.
REORG_AUDIT_DEPTH = int(os.environ.get("REORG_AUDIT_DEPTH", "36"))
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
        return verify._rpc_call(rpc_url, method, params)  # correct Basic-Auth handling lives in verify.py

    def _tip():
        # A node still in initial block download reports a stale height; abstain
        # from the tip so the explorers serve it (hashes for synced heights are
        # still valid -- getblockhash answers from the header index).
        info = rpc("getblockchaininfo", [])
        if info.get("initialblockdownload"):
            raise RuntimeError(f"node in initial block download (height {info.get('blocks')})")
        return int(info["blocks"])

    return {
        "name": "core",
        "tip": _tip,
        "hashes": lambda start, end: [verify._normalize(rpc("getblockhash", [h])) for h in range(start, end + 1)],
        # getblockheader (not getblock) so a pruned node can answer for any height.
        "timestamp": lambda block_hash: _fmt_ts(rpc("getblockheader", [block_hash])["time"]),
    }


def _providers():
    """Ordered sources: self-hosted Core first (if configured), then explorers."""
    provs = []
    core = _core_provider()
    if core:
        provs.append(core)
    provs += [_explorer_provider(n, b, f) for n, b, f in EXPLORERS]
    return provs


# user:pass@ with an OPTIONAL scheme:// prefix (so a bare `user:pass@host` in an
# error is redacted too, not only a full URL).
_CRED_RE = re.compile(r"(\b[\w+.-]+://)?[^\s/@:]+:[^\s/@]+@")


def _redact(e):
    """Strip credentials from an error before it reaches the PUBLIC status.json /
    workflow logs. Covers URL userinfo (with or without a scheme) AND any known
    secret VALUE -- the CF Access service-token secret never appears as userinfo
    (it's a header), so blunt-replace it by value too. Secrets must never leak."""
    s = _CRED_RE.sub(lambda m: (m.group(1) or "") + "***@", str(e))
    for var in ("CF_ACCESS_CLIENT_SECRET", "BITCOIN_RPC_PASSWORD"):
        val = os.environ.get(var)
        if val and val in s:
            s = s.replace(val, "***")
    return s


def _call(providers, op, *args):
    """Run `op` on each provider in turn; return (result, provider_name) for the
    first success. If every source fails, raise with all of their errors so the
    cron log says why."""
    errors = []
    for p in providers:
        try:
            return p[op](*args), p["name"]
        except Exception as e:  # network / HTTP / parse / RPC -- try the next source
            errors.append(f"{p['name']}: {_redact(e)}")
    raise RuntimeError(f"all sources failed for '{op}' -> " + " | ".join(errors))


def _probe_sources(providers):
    """Probe every source's chain tip (health check), in preference order.

    Returns [{name, ok, tip, ms} | {name, ok: False, error, ms}]. Published as
    status.json so the operator can see whether each source -- especially a
    self-hosted Core node -- answered on the last refresh."""
    out = []
    for p in providers:
        t0 = time.monotonic()
        entry = {"name": p["name"]}
        try:
            entry["tip"] = int(p["tip"]())
            entry["ok"] = True
        except Exception as e:  # unreachable / auth / IBD -- recorded, not fatal
            entry["ok"] = False
            entry["error"] = _redact(e)[:200]
        entry["ms"] = int((time.monotonic() - t0) * 1000)
        out.append(entry)
    return out


def _fingerprint(hash_list):
    """Short, stable fingerprint of an ordered hash list, for disagreement logs."""
    return hashlib.sha256("".join(hash_list).encode()).hexdigest()[:12]


def _agreed_hashes(providers, start, end):
    """Fetch the window's 144 hashes from every available source and return the
    list that >= MIN_AGREEMENT of them agree on, plus the agreeing source names.

    Raises if no list reaches the threshold (sources disagree, or too few are
    reachable). The 144 hashes are the inputs baked into the *irreversible*
    commitment chain, so the caller HOLDS the draw rather than commit hashes that
    only a single (possibly bad / forked / compromised) explorer vouches for."""
    results, errors = {}, []
    for p in providers:
        try:
            results[p["name"]] = p["hashes"](start, end)
        except Exception as e:  # an unreachable source simply doesn't get a vote
            errors.append(f"{p['name']}: {_redact(e)}")
    groups = []  # [(hash_list, [source names])], most-agreed first
    for name, hl in results.items():
        for g in groups:
            if g[0] == hl:
                g[1].append(name)
                break
        else:
            groups.append((hl, [name]))
    groups.sort(key=lambda g: len(g[1]), reverse=True)
    if groups and len(groups[0][1]) >= MIN_AGREEMENT:
        return groups[0][0], groups[0][1]
    seen = "; ".join(f"{'+'.join(names)}={_fingerprint(hl)}" for hl, names in groups) or "none returned hashes"
    raise RuntimeError(
        f"need {MIN_AGREEMENT} agreeing sources for heights {start}-{end}; saw {seen}"
        + ("" if not errors else " | unreachable: " + " | ".join(errors)))


def _safe_timestamp(providers, block_hash):
    """The display-only timestamp must never sink an otherwise-good draw: the 144
    hashes are quorum-agreed and already determine the result, so a transient
    block-detail outage falls back to null (artifacts / feed tolerate it) instead
    of aborting the commit loop after the hashes were already agreed."""
    try:
        return _call(providers, "timestamp", block_hash)[0]
    except Exception as e:
        print(f"WARNING: timestamp unavailable (using null): {_redact(e)}")
        return None


def _reorg_audit_one(providers, d, confirmed_tip):
    """Re-verify one already-published draw against the live chain. A draw is
    committed at only CONFIRMATIONS depth, so a deeper reorg could in theory alter
    its window after the fact. Returns a warning string on a RESULT mismatch -- the
    caller ALARMS, it never rewrites (the commitment chain makes a silent rewrite
    illegal). None when the draw still matches or is buried past the reorg horizon."""
    if confirmed_tip - d["end_height"] >= REORG_AUDIT_DEPTH:
        return None  # buried far past any feasible reorg -- effectively immutable
    try:
        hashes, _ = _agreed_hashes(providers, d["start_height"], d["end_height"])
    except Exception as e:
        print(f"reorg-audit: skip draw {d['id']} (re-fetch didn't reach quorum: {_redact(e)})")
        return None
    front, back, _ = verify.generate(hashes)
    if front == d["front"] and back == d["back"]:
        return None
    return (f"REORG/MISMATCH at draw {d['id']}: published {d['front']}+{d['back']}, chain now "
            f"yields {front}+{back} -- a deep reorg may have altered a committed window; "
            f"NOT auto-correcting (history is append-only)")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "site", "index.json")
    with open(path) as f:
        data = json.load(f)
    draws = data["draws"]

    next_id = (draws[-1]["id"] + 1) if draws else 0
    prev_commitment = draws[-1]["commitment"] if draws else verify.GENESIS_PREV

    providers = _providers()
    probes = _probe_sources(providers)
    for s in probes:
        print(f"source {s['name']}: " + (f"ok tip={s['tip']} ({s['ms']}ms)" if s["ok"]
                                         else f"FAIL ({s.get('error')})"))
    healthy = [s for s in probes if s["ok"]]
    # Confirmation depth gets the SAME quorum as the hashes: gate window maturity on
    # the MIN_AGREEMENT-th highest healthy tip, never a single source. A lone
    # compromised/buggy source reporting an inflated tip then can't pull an
    # under-confirmed window forward (the agreement check only proves hashes match,
    # not that the window is buried deep enough). Too few healthy sources -> no draw,
    # but still publish health status; the workflow alarms after the publish step.
    confirmed_tip = None
    note = None
    last_end = draws[-1]["end_height"] if draws else -1
    if len(healthy) >= MIN_AGREEMENT:
        quorum_tip = sorted((s["tip"] for s in healthy), reverse=True)[MIN_AGREEMENT - 1]
        if quorum_tip < last_end:
            # Two+ sources agree on a tip BELOW the last committed window -- impossible
            # for a real chain (that window was confirmed when the tip was higher
            # still). Don't let bad/forked source data gate maturity; record + alarm.
            note = (f"quorum tip {quorum_tip} regressed below the last committed window "
                    f"(end {last_end}) -- bad source data; no draw this run")
            print("WARNING: " + note)
        else:
            lead = next(s["name"] for s in healthy if s["tip"] >= quorum_tip)
            print(f"tip {quorum_tip} (quorum of {MIN_AGREEMENT}; leading source {lead})")
            confirmed_tip = quorum_tip - CONFIRMATIONS
    elif healthy:
        print(f"WARNING: only {len(healthy)} healthy source(s) < MIN_SOURCE_AGREEMENT="
              f"{MIN_AGREEMENT}; publishing health status only, no draw")
    else:
        print("WARNING: ALL SOURCES DOWN -- no chain tip; publishing health status only")
    added = 0
    held = None
    while confirmed_tip is not None and added < MAX_NEW:
        start, end = verify.heights_for(next_id)
        if end > confirmed_tip:
            break  # window not fully confirmed yet
        try:
            hashes, agreed_by = _agreed_hashes(providers, start, end)
        except Exception as e:
            # Hold this draw (and every later one) until independent sources agree:
            # never bake unverified hashes into the irreversible commitment chain.
            held = next_id
            print(f"WARNING: holding draw {next_id} -- {e}")
            break
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
            "timestamp": _safe_timestamp(providers, hashes[-1]),
        })
        prev_commitment = commitment
        next_id += 1
        added += 1
        print(f"committed draw {draws[-1]['id']} (agree: {', '.join(agreed_by)})")

    # Reorg self-audit: re-verify EVERY already-published draw still within the
    # reorg horizon (REORG_AUDIT_DEPTH) against the live chain -- newest first,
    # stopping once a draw is buried (older ones are even more buried). Runs on
    # every refresh, not just idle ones; draws committed in THIS run were already
    # verified as they were drawn, so skip those. A mismatch ALARMS (via note); it
    # never rewrites (the commitment chain makes a silent rewrite illegal).
    if confirmed_tip is not None and note is None and draws:
        fresh_ids = set(d["id"] for d in draws[len(draws) - added:]) if added else set()
        for d in reversed(draws):
            if confirmed_tip - d["end_height"] >= REORG_AUDIT_DEPTH:
                break  # buried past the horizon -- and so is everything older
            if d["id"] in fresh_ids:
                continue
            note = _reorg_audit_one(providers, d, confirmed_tip)
            if note:
                break

    head = verify.head_for(draws)

    if added:
        data["count"] = len(draws)
        data["head"] = head
        artifacts.atomic_write_json(path, data, separators=(",", ":"))

    # Always (re)write head.json next to index.json, even on a no-op refresh: the
    # publish workflow only carries index.json forward, so without this head.json
    # would 404 after any refresh that adds 0 draws (the common case).
    artifacts.atomic_write_json(os.path.join(os.path.dirname(path), "head.json"), head)

    # Downstream beacon artifacts (latest.json + feed.json), refreshed every run
    # like head.json so consumers always see the current head even on a no-op.
    artifacts.write_beacon(os.path.dirname(path), draws, head)
    # Per-source health of THIS refresh (the operator's RPC monitor).
    artifacts.write_status(os.path.dirname(path), probes, time.time(), head,
                           added=added, held=held, note=note)

    print(f"ADDED={added} total={len(draws)} latest={(draws[-1]['id'] if draws else None)}"
          + (f" HELD={held}" if held is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
