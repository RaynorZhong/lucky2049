#!/usr/bin/env python3
"""
Gate between the two jobs of .github/workflows/anchor-head.yml.

The `stamp` job installs and runs third-party code (the OpenTimestamps client and
its dependencies, from PyPI). It is therefore treated as UNTRUSTED: it gets a
read-only token and hands its `anchors/` output over as a build artifact. The
`commit` job holds the token that can push to main and runs no third-party code;
before committing it passes the artifact through this script, which copies a file
into anchors/ only if ALL of these hold for the whole batch:

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
    one may only be replaced by an attested one that is not smaller.

One violation rejects the entire batch and changes nothing (exit 1): the stamper is
either broken or compromised, and a human should look.

What the gate does NOT do: check a proof's attestation against the Bitcoin chain. It
confines the stamper to anchors/ and binds each proof to its published head, but a new
or freshly upgraded proof is only as honest as the stamp job until someone runs
`ots verify` on it (see anchors/README.md). Stdlib only.

Usage:  python scripts/accept_anchors.py <incoming-dir> [anchors-dir] [site-base-url-or-dir]
        python scripts/accept_anchors.py --check-pending [anchors-dir]
"""
import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ANCHORS = os.path.join(REPO, "anchors")
DEFAULT_SITE = "https://lucky2049.com"

NAME_RE = re.compile(r"([0-9]{6})\.head\.json(\.ots)?")     # always used with fullmatch()
# OpenTimestamps detached-proof file: 31-byte magic, version 1, the file-hash op
# (0x08 = SHA-256), the 32-byte digest of the stamped file, then the timestamp tree.
OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
PENDING_ATTESTATION = bytes.fromhex("83dfe30d2ef90c8e")
BITCOIN_ATTESTATION = bytes.fromhex("0588960d73d71901")   # BitcoinBlockHeaderAttestation tag
_UNARY_OPS = {0x02, 0x03, 0x08, 0x67, 0xf2, 0xf3}        # sha1 ripemd160 sha256 keccak256 reverse hexlify
_BINARY_OPS = {0xf0, 0xf1}                               # append / prepend <varbytes>
MAX_HEAD_BYTES = 1024
MAX_OTS_BYTES = 16 * 1024      # real proofs: ~0.8 KiB pending, ~5.4 KiB at most once attested
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


def parse_proof(ots):
    """Strictly parse a detached OpenTimestamps proof. Returns (file_digest, attested)
    where `attested` is True if it carries at least one Bitcoin block attestation.
    Raises ValueError on anything that is not a complete, well-formed proof."""
    r = _Reader(ots)
    if r.take(len(OTS_MAGIC)) != OTS_MAGIC:
        raise ValueError("bad magic")
    if r.varuint() != 1:
        raise ValueError("unsupported version")
    if r.take(1) != b"\x08":
        raise ValueError("file hash is not SHA-256")
    digest = r.take(32)
    kinds = []

    def item(tag):
        """Consume one item; True if it was an op (a nested timestamp follows)."""
        if tag == 0x00:
            kind = r.take(8)
            payload = _Reader(r.varbytes(8192))
            if kind == PENDING_ATTESTATION:
                payload.varbytes(1000)                      # calendar URI
            elif kind == BITCOIN_ATTESTATION:
                payload.varuint()                           # block height
            else:
                raise ValueError("unknown attestation type " + kind.hex())
            if payload.i != len(payload.b):
                raise ValueError("trailing bytes in an attestation")
            kinds.append(kind)
            return False
        if tag in _BINARY_OPS:
            r.varbytes(4096, 1)
            return True
        if tag in _UNARY_OPS:
            return True
        raise ValueError("unknown op 0x%02x" % tag)

    # timestamp := (0xff item)* item ;  item := attestation | op timestamp
    # Iterative on purpose: real proofs are hash chains hundreds of ops deep.
    need = 1
    while need:
        tag = r.take(1)[0]
        if tag == 0xFF:
            if item(r.take(1)[0]):
                need += 1          # a branch: its nested timestamp comes first
        elif not item(tag):
            need -= 1              # an attestation closes this timestamp
    if r.i != len(ots):
        raise ValueError("trailing bytes after the proof")
    return digest, BITCOIN_ATTESTATION in kinds


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
        req = urllib.request.Request(base.rstrip("/") + "/index.json",
                                     headers={"User-Agent": "lucky2049-anchor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    else:
        with open(os.path.join(base, "index.json")) as f:
            data = json.load(f)
    return data.get("draws") or []


def _published_draw(site, draw_id, sleep):
    """The published draw `draw_id`, re-reading index.json a few times in case this
    job hit a staler CDN copy than the one the stamp job anchored."""
    for attempt in range(PUBLISHED_RETRIES):
        draws = load_draws(site)
        if draw_id < len(draws):
            d = draws[draw_id]
            if d.get("id") != draw_id:
                raise Rejected(f"index.json is inconsistent at position {draw_id} -- not a published draw")
            return d
        if attempt + 1 < PUBLISHED_RETRIES and site.startswith(("http://", "https://")):
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


def accept(incoming, anchors_dir=DEFAULT_ANCHORS, site=DEFAULT_SITE, sleep=time.sleep):
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
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--check-pending":
        anchors_dir = argv[1] if len(argv) > 1 else DEFAULT_ANCHORS
        stale = stale_pending(anchors_dir)
        if stale:
            print(f"ERROR: {len(stale)} proof(s) more than {MAX_PENDING_BEHIND} draws old still have no "
                  f"Bitcoin attestation -- `ots upgrade` is not working: {', '.join(stale[:10])}")
            return 1
        print("all proofs older than the last few draws carry a Bitcoin attestation")
        return 0
    if not argv:
        print(__doc__)
        return 2
    incoming = argv[0]
    anchors_dir = argv[1] if len(argv) > 1 else DEFAULT_ANCHORS
    site = argv[2] if len(argv) > 2 else DEFAULT_SITE
    try:
        written = accept(incoming, anchors_dir, site)
    except Rejected as e:
        print(f"REJECTED the stamp job's output, nothing committed: {e}")
        return 1
    print(f"accepted {len(written)} file(s): {', '.join(written) or '(nothing new)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
