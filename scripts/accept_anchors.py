#!/usr/bin/env python3
"""
Gate between the two jobs of .github/workflows/anchor-head.yml.

The `stamp` job talks to untrusted external OpenTimestamps calendar servers using
a read-only token and hands its `anchors/` output over as a build artifact. The
`commit` job holds the token that can push to main; before committing it passes
the artifact through this script, which copies a file into anchors/ only if ALL
of these hold for the whole batch:

  - every name is exactly NNNNNN.head.json or NNNNNN.head.json.ots (ASCII digits),
    a small regular file;
  - at most ONE head is new, it is newer than every head already anchored, and it is
    byte-for-byte what scripts/anchor_head.py writes for the PUBLISHED history (the
    commitment of draw NNNNNN, count NNNNNN+1) -- so a compromised stamper can
    neither anchor a head the site never published nor flood main with files;
  - an EXISTING head file is never modified;
  - every proof parses, start to end, as an OpenTimestamps proof (known ops, only
    pending/Bitcoin attestations, no trailing bytes) of the SHA-256 of its head file;
  - a proof that already carries a Bitcoin attestation never changes again; a pending
    one may only be replaced by an attested one that is not smaller;
  - when `--verify-chain` is passed, every new or freshly upgraded Bitcoin attestation
    must evaluate to the exact 32-byte block header `merkle_root` confirmed by >= 2
    independent Bitcoin mainnet sources.

One violation rejects the entire batch and changes nothing (exit 1): the stamper is
either broken or compromised, and a human should look. Stdlib only.

Usage:  python scripts/accept_anchors.py [--verify-chain] <incoming-dir> [anchors-dir] [site-base-url-or-dir]
        python scripts/accept_anchors.py --check-pending [anchors-dir]
"""
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import time
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
import verify  # noqa: E402 — stdlib-only HTTP Keep-Alive + RPC helpers
DEFAULT_ANCHORS = os.path.join(REPO, "anchors")
DEFAULT_SITE = "https://lucky2049.com"
ALLOWED_CALENDARS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
    "https://btc.calendar.catallaxy.com",
)
_NONPUBLIC_V6_NETS = (
    ipaddress.ip_network("::/96"),          # deprecated IPv4-compatible (e.g. ::127.0.0.1)
    ipaddress.ip_network("64:ff9b::/96"),   # RFC 6052 well-known NAT64 prefix
    ipaddress.ip_network("64:ff9b:1::/48"), # RFC 8215 local-use NAT64 prefix
)

NAME_RE = re.compile(r"([0-9]{6})\.head\.json(\.ots)?")     # always used with fullmatch()
# OpenTimestamps detached-proof file: 31-byte magic, version 1, the file-hash op
# (0x08 = SHA-256), the 32-byte digest of the stamped file, then the timestamp tree.
OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
PENDING_ATTESTATION = bytes.fromhex("83dfe30d2ef90c8e")
BITCOIN_ATTESTATION = bytes.fromhex("0588960d73d71901")   # BitcoinBlockHeaderAttestation tag
_UNARY_OPS = {0x08, 0xF2, 0xF3}                           # sha256 reverse hexlify (matches static/verify.js)
_BINARY_OPS = {0xF0, 0xF1}                                # append / prepend <varbytes>
MAX_HEAD_BYTES = 1024
MAX_OTS_BYTES = 16 * 1024      # real proofs: ~0.8 KiB pending, ~5.4 KiB at most once attested
MAX_ATTESTATIONS = 32          # bounds explorer-query fanout + duplicate-branch floods
MAX_BRANCH_DEPTH = 64          # bounds simultaneous 0xFF branches in a single proof
MAX_OTS_OPS = 16384            # bounds total hashing/transform steps per proof
MAX_NEW_HEADS = 1              # anchor_head.py only ever writes the current head
# ~1 draw/day and proofs upgrade within a day, so a proof this many draws behind the
# newest anchor that STILL has no Bitcoin attestation means upgrading is broken.
MAX_PENDING_BEHIND = 3
# index.json is served with max-age=600; the commit job may briefly see an older copy
# than the stamp job did. Re-read a few times before calling a head "unpublished".
PUBLISHED_RETRIES = 3
PUBLISHED_RETRY_SLEEP_S = 60


class Rejected(Exception):
    """The incoming batch violates a rule; nothing was copied."""


class _Reader:
    def __init__(self, data):
        self.b, self.i = data, 0

    def take(self, n):
        if self.i + n > len(self.b):
            raise ValueError("truncated")
        out = self.b[self.i:self.i + n]
        self.i += n
        return out

    def varuint(self):
        value = shift = 0
        while True:
            c = self.take(1)[0]
            value |= (c & 0x7F) << shift
            if not c & 0x80:
                return value
            shift += 7
            if shift > 63:
                raise ValueError("varuint too long")

    def varbytes(self, max_len, min_len=0):
        n = self.varuint()
        if not min_len <= n <= max_len:
            raise ValueError("bad length %d" % n)
        return self.take(n)


def _apply_ots_op(tag, arg, msg):
    """Evaluate one OpenTimestamps unary/binary operation with bounded message size."""
    if tag == 0x08:
        return hashlib.sha256(msg).digest()
    if tag == 0xF0:
        if len(msg) + len(arg) > 16384:
            raise ValueError("OTS message exceeds maximum length")
        return msg + arg
    if tag == 0xF1:
        if len(msg) + len(arg) > 16384:
            raise ValueError("OTS message exceeds maximum length")
        return arg + msg
    if tag == 0xF2:
        return msg[::-1]
    if tag == 0xF3:
        if len(msg) * 2 > 16384:
            raise ValueError("OTS hexlify exceeds maximum length")
        return msg.hex().encode("ascii")
    raise ValueError("unsupported op 0x%02x in evaluated proof" % tag)


def is_public_ip(ip):
    if ip.version == 6:
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif any(ip in net for net in _NONPUBLIC_V6_NETS):
            return False
    return (
        ip.is_global
        and not (ip.is_private or ip.is_loopback or ip.is_link_local
                 or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
    )


def check_safe_calendar_url(url, resolve_dns=False, getaddrinfo=None):
    """Ensure calendar URLs (especially untrusted PendingAttestation URIs) use HTTPS
    on standard port 443 and a public hostname, never userinfo/queries/localhost or
    non-global/NAT64 IP space."""
    if not url or any(ord(c) <= 0x20 or ord(c) >= 0x7F for c in url):
        raise ValueError(f"unsafe calendar URI: {url!r}")
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError(f"unsafe calendar URI: {url!r}")
    try:
        port = parts.port or 443
    except ValueError as e:
        raise ValueError(f"invalid calendar port in {url!r}: {e}") from e
    if port != 443:
        raise ValueError(f"disallowed non-443 calendar port in {url!r}")
    host = parts.hostname.rstrip(".").lower()
    if (not host or host in ("localhost", "localhost.localdomain")
            or host.endswith((".localhost", ".local", ".localdomain", ".internal"))):
        raise ValueError(f"disallowed local calendar host: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not is_public_ip(ip):
            raise ValueError(f"disallowed non-public calendar IP: {host!r}")
        return
    if resolve_dns:
        resolve = getaddrinfo or socket.getaddrinfo
        try:
            addrs = resolve(host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"unresolvable calendar host {host!r}: {e}") from e
        if not addrs:
            raise ValueError(f"no DNS addresses for calendar host: {host!r}")
        for _, _, _, _, sockaddr in addrs:
            resolved = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
            if not is_public_ip(resolved):
                raise ValueError(f"disallowed non-public calendar IP {resolved} for {host!r}")


def _consume_attestation(r):
    """Read an attestation body after tag 0x00; return (kind, value)."""
    kind = r.take(8)
    payload = _Reader(r.varbytes(8192))
    if kind == PENDING_ATTESTATION:
        raw_uri = payload.varbytes(1000, 8)
        try:
            val = raw_uri.decode("ascii")
        except UnicodeDecodeError as e:
            raise ValueError("non-ASCII pending calendar URI") from e
        check_safe_calendar_url(val, resolve_dns=False)
    elif kind == BITCOIN_ATTESTATION:
        val = payload.varuint()
    else:
        raise ValueError("unknown attestation type " + kind.hex())
    if payload.i != len(payload.b):
        raise ValueError("trailing bytes in an attestation")
    return kind, val


def walk_ots(ots, evaluate=False):
    """Iteratively parse (and optionally evaluate) a detached OpenTimestamps proof.
    Returns (file_digest, bitcoin_attestations, pending_leaves):
      - bitcoin_attestations: [(height, merkle_root_hex)] when evaluate=True, else [height]
      - pending_leaves: [(was_ff, item_start, item_end, uri, commit_msg)]
    Uses O(branches) stack space so 5000-op linear hash chains never recurse."""
    r = _Reader(ots)
    if r.take(len(OTS_MAGIC)) != OTS_MAGIC:
        raise ValueError("bad magic")
    if r.varuint() != 1:
        raise ValueError("unsupported version")
    if r.take(1) != b"\x08":
        raise ValueError("file hash is not SHA-256")
    digest = r.take(32)
    cur_msg = digest
    stack = []
    btc, pending = [], []
    ops = raw_btc = 0

    while True:
        ops += 1
        if ops > MAX_OTS_OPS:
            raise ValueError("too many OTS operations in one proof")
        tag = r.take(1)[0]
        was_ff = (tag == 0xFF)
        if was_ff:
            if len(stack) >= MAX_BRANCH_DEPTH:
                raise ValueError("too many nested OTS branches")
            stack.append(cur_msg)
            item_start = r.i
            itag = r.take(1)[0]
        else:
            item_start = r.i - 1
            itag = tag

        if itag == 0x00:
            kind, val = _consume_attestation(r)
            item_end = r.i
            if kind == BITCOIN_ATTESTATION:
                raw_btc += 1
                if raw_btc > MAX_ATTESTATIONS:
                    raise ValueError("too many Bitcoin attestations in one proof")
                if evaluate:
                    if len(cur_msg) != 32:
                        raise ValueError("Bitcoin attestation leaf digest is not 32 bytes")
                    pair = (val, cur_msg[::-1].hex())
                    if pair not in btc:
                        btc.append(pair)
                else:
                    btc.append(val)
            else:
                pending.append((was_ff, item_start, item_end, val, cur_msg))
                if len(pending) > MAX_ATTESTATIONS:
                    raise ValueError("too many pending attestations in one proof")
            if not stack:
                break
            cur_msg = stack.pop()
        elif itag in _BINARY_OPS:
            arg = r.varbytes(4096, 1)
            if evaluate:
                cur_msg = _apply_ots_op(itag, arg, cur_msg)
        elif itag in _UNARY_OPS:
            if evaluate:
                cur_msg = _apply_ots_op(itag, None, cur_msg)
        else:
            raise ValueError("unknown op 0x%02x" % itag)

    if r.i != len(ots):
        raise ValueError("trailing bytes after the proof")
    return digest, btc, pending


def split_timestamp_items(ts_bytes):
    """Validate a raw Timestamp subtree and return its top-level item byte slices
    (each without a leading 0xFF branch marker)."""
    parse_proof(OTS_MAGIC + b"\x01\x08" + (b"\x00" * 32) + ts_bytes)
    r = _Reader(ts_bytes)
    items = []
    while r.i < len(ts_bytes) and ts_bytes[r.i] == 0xFF:
        r.take(1)
        start = r.i
        need = 1
        while need:
            tag = r.take(1)[0]
            itag = r.take(1)[0] if tag == 0xFF else tag
            if itag == 0x00:
                _consume_attestation(r)
                if tag != 0xFF:
                    need -= 1
            elif itag in _BINARY_OPS:
                r.varbytes(4096, 1)
                if tag == 0xFF:
                    need += 1
            elif itag in _UNARY_OPS:
                if tag == 0xFF:
                    need += 1
            else:
                raise ValueError("unknown op 0x%02x" % itag)
        items.append(ts_bytes[start:r.i])
    items.append(ts_bytes[r.i:])
    return items


def parse_proof(ots):
    """Strictly parse and evaluate a detached OpenTimestamps proof. Returns
    (file_digest, attested) where `attested` is True if it carries >= 1 valid
    32-byte Bitcoin block attestation."""
    digest, btc, _ = walk_ots(ots, evaluate=True)
    return digest, bool(btc)


def eval_bitcoin_attestations(ots, head_bytes=None):
    """Evaluate a detached OpenTimestamps proof and return [(height, merkle_root_hex)]
    for every BitcoinBlockHeaderAttestation leaf. If `head_bytes` is provided, also
    verifies the proof header commits to SHA256(head_bytes)."""
    digest, btc, _ = walk_ots(ots, evaluate=True)
    if head_bytes is not None and digest != hashlib.sha256(head_bytes).digest():
        raise ValueError("proof digest is not the SHA-256 of its head file")
    return btc


def proof_problem(ots, head_bytes):
    """None if `ots` is a well-formed OpenTimestamps proof of exactly `head_bytes`."""
    try:
        digest, _ = parse_proof(ots)
    except (ValueError, IndexError) as e:
        return f"not an OpenTimestamps proof ({e})"
    if digest != hashlib.sha256(head_bytes).digest():
        return "proof digest is not the SHA-256 of its head file"
    return None


def is_attested(ots):
    try:
        return parse_proof(ots)[1]
    except (ValueError, IndexError):
        return False


def canonical_head(draw_id, commitment, algo_version):
    """The exact bytes scripts/anchor_head.py writes (and the existing proofs stamp)."""
    rec = {"head": commitment, "draw_id": draw_id, "count": draw_id + 1, "algo_version": algo_version}
    return (json.dumps(rec, indent=2, sort_keys=True) + "\n").encode()


def load_draws(base):
    if base.startswith(("http://", "https://")):
        parts = urllib.parse.urlsplit(base)
        path = parts.path.rstrip("/") + "/index.json"
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
        data = json.loads(verify._http_get(url, timeout=60))
    else:
        with open(os.path.join(base, "index.json")) as f:
            data = json.load(f)
    return data.get("draws") or []


def _published_draw(site, draw_id, sleep):
    """The published draw `draw_id`, re-reading index.json a few times in case this
    job hit a staler CDN copy than the one the stamp job anchored."""
    is_http = site.startswith(("http://", "https://"))
    for attempt in range(PUBLISHED_RETRIES):
        target = f"{site.rstrip('/')}?v={draw_id}-{attempt}" if (is_http and attempt > 0) else site
        draws = load_draws(target)
        if draw_id < len(draws):
            d = draws[draw_id]
            if d.get("id") != draw_id:
                raise Rejected(f"index.json is inconsistent at position {draw_id} -- not a published draw")
            return d
        if attempt + 1 < PUBLISHED_RETRIES and is_http:
            print(f"draw {draw_id} not in the published index yet (stale CDN copy?) -- retrying in "
                  f"{PUBLISHED_RETRY_SLEEP_S}s", flush=True)
            sleep(PUBLISHED_RETRY_SLEEP_S)
    raise Rejected(f"{draw_id} is not a published draw (index.json has {len(draws)} draws; if a draw "
                   f"was published minutes ago this may be a stale CDN copy -- re-run later)")


def _anchor_names(directory):
    return [n for n in os.listdir(directory) if NAME_RE.fullmatch(n)]


def _read_incoming(incoming):
    files = {}
    for name in sorted(os.listdir(incoming)):
        path = os.path.join(incoming, name)
        st = os.lstat(path)
        if not NAME_RE.fullmatch(name):
            raise Rejected(f"unexpected file in the stamp output: {name!r}")
        if not stat.S_ISREG(st.st_mode):
            raise Rejected(f"{name} is not a regular file")
        limit = MAX_OTS_BYTES if name.endswith(".ots") else MAX_HEAD_BYTES
        if st.st_size > limit:
            raise Rejected(f"{name} is too large ({st.st_size} bytes > {limit})")
        with open(path, "rb") as f:
            files[name] = f.read()
    return files


def _read_existing(anchors_dir, name):
    try:
        with open(os.path.join(anchors_dir, name), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _fetch_chain_merkle_roots(height):
    """Query independent Bitcoin sources for block `height`'s header merkle_root."""
    seen = {}
    if os.environ.get("BITCOIN_RPC_URL") or os.environ.get("BITCOIN_RPC_USER"):
        try:
            rpc_url = verify._core_rpc_url_from_env()
            bh = verify._normalize(verify._rpc_call(rpc_url, "getblockhash", [height]))
            hdr = verify._rpc_call(rpc_url, "getblockheader", [bh])
            seen["core"] = verify._normalize(hdr["merkleroot"])
        except Exception:
            pass
    for name, base in (("mempool", "https://mempool.space/api"),
                       ("blockstream", "https://blockstream.info/api")):
        try:
            bh = verify._normalize(verify._http_get(f"{base}/block-height/{height}"))
            blk = json.loads(verify._http_get(f"{base}/block/{bh}"))
            seen[name] = verify._normalize(blk["merkle_root"])
        except Exception:
            pass
    return seen


def verify_attestations_on_chain(name, attestations, merkle_fetcher=None, min_sources=2):
    """Confirm every Bitcoin leaf is valid on chain (zero merkle_root mismatches and
    >= 1 confirming source per leaf) AND >= 1 leaf reaches >= min_sources quorum."""
    if not attestations:
        raise Rejected(f"{name}: proof claims attestation but evaluated 0 Bitcoin leaves")
    fetch = merkle_fetcher or _fetch_chain_merkle_roots
    details = []
    confirmed = None
    for height, expected_mr in attestations:
        seen = fetch(height) or {}
        agree = [src for src, mr in seen.items() if isinstance(mr, str) and mr.lower() == expected_mr]
        mismatched = [f"{src}={mr[:12]}..." for src, mr in seen.items()
                      if isinstance(mr, str) and mr.lower() != expected_mr]
        if mismatched:
            raise Rejected(
                f"{name}: Bitcoin block merkle_root mismatch -> height {height} merkle_root mismatch "
                f"(proof {expected_mr[:12]}... vs {', '.join(mismatched)})"
            )
        if not agree:
            raise Rejected(
                f"{name}: Bitcoin header quorum not met -> height {height} reached 0/{min_sources} sources"
            )
        if len(agree) >= min_sources and confirmed is None:
            confirmed = {"height": height, "merkle_root": expected_mr, "sources": agree}
        elif len(agree) < min_sources:
            details.append(f"height {height} reached only {len(agree)}/{min_sources} sources ({', '.join(agree)})")
    if confirmed is not None:
        return confirmed
    raise Rejected(f"{name}: Bitcoin header quorum not met -> {'; '.join(details)}")


def _check_allowed_calendars(name, ots_bytes):
    _, _, pending = walk_ots(ots_bytes, evaluate=False)
    for _, _, _, uri, _ in pending:
        if uri.rstrip("/") not in ALLOWED_CALENDARS:
            raise Rejected(f"{name}: untrusted pending calendar URI {uri!r} (not in allowlist)")


def accept(incoming, anchors_dir=DEFAULT_ANCHORS, site=DEFAULT_SITE, sleep=time.sleep,
           verify_chain=False, merkle_fetcher=None):
    """Validate the whole batch, then copy what is new or upgraded. Returns the list
    of file names written. Raises Rejected (having changed nothing) on any violation."""
    files = _read_incoming(incoming)
    heads = {n: b for n, b in files.items() if n.endswith(".json")}
    proofs = {n: b for n, b in files.items() if n.endswith(".ots")}
    os.makedirs(anchors_dir, exist_ok=True)
    anchored = [int(n[:6]) for n in _anchor_names(anchors_dir) if n.endswith(".json")]
    newest_anchored = max(anchored) if anchored else -1
    to_write = {}

    new_heads = [n for n in heads if _read_existing(anchors_dir, n) is None]
    if len(new_heads) > MAX_NEW_HEADS:
        raise Rejected(f"{len(new_heads)} new heads in one batch (at most {MAX_NEW_HEADS} expected)")
    for name, data in heads.items():
        existing = _read_existing(anchors_dir, name)
        if existing is not None:
            if existing != data:
                raise Rejected(f"existing anchor {name} would be modified")
            continue
        draw_id = int(name[:6])
        if draw_id <= newest_anchored:
            raise Rejected(f"new anchor {name} is older than the newest anchored head ({newest_anchored:06d})")
        d = _published_draw(site, draw_id, sleep)
        if data != canonical_head(draw_id, d["commitment"], d.get("algo_version", "v1")):
            raise Rejected(f"{name} does not match the published history at draw {draw_id}")
        if name + ".ots" not in proofs:
            raise Rejected(f"new anchor {name} has no proof")
        to_write[name] = data

    for name, data in proofs.items():
        head_name = name[:-len(".ots")]
        head_bytes = heads.get(head_name)
        if head_bytes is None:
            head_bytes = _read_existing(anchors_dir, head_name)
        if head_bytes is None:
            raise Rejected(f"proof {name} has no head file")
        problem = proof_problem(data, head_bytes)
        if problem:
            raise Rejected(f"{name}: {problem}")
        existing = _read_existing(anchors_dir, name)
        if existing is None:
            if verify_chain:
                _check_allowed_calendars(name, data)
                if is_attested(data):
                    try:
                        atts = eval_bitcoin_attestations(data, head_bytes)
                    except ValueError as e:
                        raise Rejected(f"{name}: invalid Bitcoin attestation path ({e})") from e
                    verify_attestations_on_chain(name, atts, merkle_fetcher=merkle_fetcher)
            to_write[name] = data
            continue
        if existing == data:
            continue
        if is_attested(existing):
            raise Rejected(f"{name} already carries a Bitcoin attestation and must not change")
        if not is_attested(data):
            # pending -> a different pending proof: never seen from an honest upgrade.
            # Keep the proof we have (it holds the real calendar commitments).
            print(f"ignoring {name}: still pending but different from the committed proof")
            continue
        if len(data) < len(existing):
            raise Rejected(f"{name} would shrink ({len(existing)} -> {len(data)} bytes)")
        if verify_chain:
            _check_allowed_calendars(name, data)
            try:
                atts = eval_bitcoin_attestations(data, head_bytes)
            except ValueError as e:
                raise Rejected(f"{name}: invalid Bitcoin attestation path ({e})") from e
            verify_attestations_on_chain(name, atts, merkle_fetcher=merkle_fetcher)
        to_write[name] = data

    for name, data in sorted(to_write.items()):
        tmp = os.path.join(anchors_dir, ".tmp-" + name)
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(anchors_dir, name))
    return sorted(to_write)


def stale_pending(anchors_dir=DEFAULT_ANCHORS, max_behind=MAX_PENDING_BEHIND):
    """Proofs that still lack a Bitcoin attestation (or no longer parse) although they
    are more than `max_behind` draws older than the newest anchor -- i.e. upgrading
    has stopped working."""
    ids = sorted(int(n[:6]) for n in _anchor_names(anchors_dir) if n.endswith(".ots"))
    out = []
    for i in ids:
        if ids[-1] - i <= max_behind:
            continue
        name = "%06d.head.json.ots" % i
        with open(os.path.join(anchors_dir, name), "rb") as f:
            if not is_attested(f.read()):
                out.append(name)
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--check-pending":
        anchors_dir = argv[1] if len(argv) > 1 else DEFAULT_ANCHORS
        stale = stale_pending(anchors_dir)
        if stale:
            print(f"ERROR: {len(stale)} proof(s) more than {MAX_PENDING_BEHIND} draws old still have no "
                  f"Bitcoin attestation -- OTS upgrade is not working: {', '.join(stale[:10])}")
            return 1
        print("all proofs older than the last few draws carry a Bitcoin attestation")
        return 0
    verify_chain = False
    if argv and argv[0] == "--verify-chain":
        verify_chain = True
        argv = argv[1:]
    if not argv:
        print(__doc__)
        return 2
    incoming = argv[0]
    anchors_dir = argv[1] if len(argv) > 1 else DEFAULT_ANCHORS
    site = argv[2] if len(argv) > 2 else DEFAULT_SITE
    try:
        written = accept(incoming, anchors_dir, site, verify_chain=verify_chain)
    except Rejected as e:
        print(f"REJECTED the stamp job's output, nothing committed: {e}")
        return 1
    print(f"accepted {len(written)} file(s): {', '.join(written) or '(nothing new)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
