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

  - Liveness: the sources are fetched concurrently and each gets a wall-clock
    budget (SOURCE_BUDGET_S); a source that failed its tip probe is not asked for
    hashes at all that run, and a slow one simply loses its vote. (The price: with
    three sources, a second one failing in the same run holds the draw until the
    next hourly run -- self-healing, never a wrong commit.) No new draw or audit
    starts after RUN_BUDGET_S; an in-flight window may still take SOURCE_BUDGET_S
    plus a timestamp lookup, so a run normally ends well inside the job timeout.

Usage:  python scripts/extend_pages.py [site/index.json]
Env:    DRAW_CONFIRMATIONS (default 6), MAX_NEW_DRAWS (default 10),
        MIN_SOURCE_AGREEMENT (default 2), SOURCE_BUDGET_S (default 300),
        RUN_BUDGET_S (default 900),
        BITCOIN_RPC_URL or BITCOIN_RPC_USER/PASSWORD/HOST/PORT (optional Core source),
        CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET (optional Cloudflare Access
        service token, when the Core endpoint sits behind Zero Trust)
"""
import datetime
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse

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
# Wall-clock budgets (seconds). A healthy source serves a 144-hash window in well
# under a minute; one that is slow-but-not-dead used to stall the whole run (each of
# 144 sequential requests may retry for ~90s), so every source gets SOURCE_BUDGET_S
# per window and then simply loses its vote. RUN_BUDGET_S bounds the run as a whole:
# past it no new draw or audit is started. Worst case is then roughly RUN_BUDGET_S +
# one SOURCE_BUDGET_S + a timestamp lookup (~25 min with the defaults), inside the
# workflow's 30-minute job timeout.
def _budget(name, default):
    """A positive, finite number of seconds from the environment, else the default
    (an empty, non-numeric, zero or negative value must not disable the drawer)."""
    try:
        v = float(os.environ.get(name) or default)
    except ValueError:
        return float(default)
    return v if math.isfinite(v) and v > 0 else float(default)


SOURCE_BUDGET_S = _budget("SOURCE_BUDGET_S", 300)
RUN_BUDGET_S = _budget("RUN_BUDGET_S", 900)
ALGO = "v1"


class TipAbstain(RuntimeError):
    """A source that deliberately declines to report a tip (a Core node in initial
    block download) but is otherwise reachable: it has no say on maturity, yet still
    votes on block hashes. Any OTHER tip-probe failure means the source is down for
    this run and is not asked for hashes at all."""

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
            raise TipAbstain(f"node in initial block download (height {info.get('blocks')})")
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
    workflow logs. Covers URL userinfo (with or without a scheme) AND known secret
    VALUES -- the CF Access service-token secret is a header (never userinfo), and a
    BITCOIN_RPC_URL password may contain '/' or '@' that the userinfo regex can't
    span, so blunt-replace those by value too. Secrets must never leak."""
    s = _CRED_RE.sub(lambda m: (m.group(1) or "") + "***@", str(e))
    secrets = [os.environ.get("CF_ACCESS_CLIENT_SECRET"), os.environ.get("BITCOIN_RPC_PASSWORD")]
    rpc = os.environ.get("BITCOIN_RPC_URL")
    if rpc:
        try:
            secrets.append(urllib.parse.urlsplit(rpc).password)   # exact pw, special chars and all
        except ValueError:
            pass
    for val in secrets:
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


def _probe_sources(providers, abstained=None):
    """Probe every source's chain tip (health check), in preference order.

    Returns [{name, ok, tip, ms} | {name, ok: False, error, ms}]. Published as
    status.json so the operator can see whether each source -- especially a
    self-hosted Core node -- answered on the last refresh. Names of sources that
    abstained (TipAbstain: reachable, just no tip) are added to `abstained`, so the
    caller can keep them as hash voters without changing the published shape."""
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
            if isinstance(e, TipAbstain) and abstained is not None:
                abstained.add(p["name"])
        entry["ms"] = int((time.monotonic() - t0) * 1000)
        out.append(entry)
    return out


def _fingerprint(hash_list):
    """Short, stable fingerprint of an ordered hash list, for disagreement logs."""
    return hashlib.sha256("".join(hash_list).encode()).hexdigest()[:12]


def _fetch_votes(providers, start, end):
    """Ask every source for the window's hashes CONCURRENTLY, each bounded by
    SOURCE_BUDGET_S of wall-clock. Returns ({name: hash_list}, [error strings]) with
    results in provider (preference) order. A source that errors or runs out of time
    simply doesn't get a vote -- its thread is a daemon and is abandoned."""
    box = {}

    def work(p):
        t0 = time.monotonic()
        try:
            box[p["name"]] = ("ok", p["hashes"](start, end), time.monotonic() - t0)
        except Exception as e:  # network / HTTP / parse / RPC
            box[p["name"]] = ("err", e, time.monotonic() - t0)

    print(f"fetching heights {start}-{end} from: {', '.join(p['name'] for p in providers) or '(no live source)'}",
          flush=True)
    threads = [threading.Thread(target=work, args=(p,), daemon=True, name=f"hashes-{p['name']}")
               for p in providers]
    for t in threads:
        t.start()
    deadline = time.monotonic() + SOURCE_BUDGET_S
    results, errors = {}, []
    for p, t in zip(providers, threads):
        t.join(max(0.0, deadline - time.monotonic()))
        got = box.get(p["name"])
        if got is None:
            errors.append(f"{p['name']}: timed out after {SOURCE_BUDGET_S:g}s")
            print(f"  {p['name']}: no vote (timed out after {SOURCE_BUDGET_S:g}s)", flush=True)
        elif got[0] == "ok":
            results[p["name"]] = got[1]
            print(f"  {p['name']}: {len(got[1])} hashes in {got[2]:.1f}s", flush=True)
        else:
            errors.append(f"{p['name']}: {_redact(got[1])}")
            print(f"  {p['name']}: no vote ({_redact(got[1])[:160]})", flush=True)
    return results, errors


def _agreed_hashes(providers, start, end):
    """Fetch the window's 144 hashes from every available source and return the
    list that >= MIN_AGREEMENT of them agree on, plus the agreeing source names.

    Raises if no list reaches the threshold (sources disagree, or too few are
    reachable). The 144 hashes are the inputs baked into the *irreversible*
    commitment chain, so the caller HOLDS the draw rather than commit hashes that
    only a single (possibly bad / forked / compromised) explorer vouches for."""
    results, errors = _fetch_votes(providers, start, end)
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

    t_run = time.monotonic()

    def out_of_time():
        return time.monotonic() - t_run >= RUN_BUDGET_S

    providers = _providers()
    abstained = set()
    probes = _probe_sources(providers, abstained)
    # Hash voters for this run: every source that answered its probe, plus any that
    # only abstained from the tip. A source whose probe FAILED is down right now --
    # asking it for 144 hashes would just burn minutes of retries for no vote.
    voters = [p for p, s in zip(providers, probes) if s["ok"] or p["name"] in abstained]
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
        if out_of_time():
            print(f"run budget ({RUN_BUDGET_S:g}s) reached -- draw {next_id} waits for the next run")
            break
        try:
            hashes, agreed_by = _agreed_hashes(voters, start, end)
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
            "timestamp": _safe_timestamp(voters, hashes[-1]),
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
            if out_of_time():
                print(f"run budget ({RUN_BUDGET_S:g}s) reached -- reorg audit of draw {d['id']} skipped this run")
                break
            note = _reorg_audit_one(voters, d, confirmed_tip)
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
