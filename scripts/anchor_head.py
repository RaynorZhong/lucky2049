#!/usr/bin/env python3
"""
Pure-stdlib OpenTimestamps head writer, stamper, and upgrader for lucky2049.

1. Reads the published snapshot's commitment head (from index.json) and writes
   anchors/<draw_id>.head.json if not already anchored.
2. When `--stamp` is passed, stamps any anchors/*.head.json lacking a companion
   .ots proof across public OpenTimestamps calendars (16-byte random nonce,
   requires >= 2 independent calendars to return valid Timestamp subtrees).
3. When `--upgrade` is passed, upgrades ONLY unattested pending .ots proofs
   (`not accept_anchors.is_attested(ots)`). Proofs that already carry >= 1
   Bitcoin block attestation are permanently immutable and never re-queried,
   preventing lagging calendars from triggering accept_anchors' immutability gate.

100% Python standard library — zero PyPI dependencies.

Usage:  python scripts/anchor_head.py [--stamp] [--upgrade] [site-base-url-or-local-dir]
"""
import argparse
import hashlib
import ipaddress
import json
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (REPO, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import accept_anchors  # noqa: E402
import verify  # noqa: E402

DEFAULT_BASE = "https://lucky2049.com"
DEFAULT_CALENDARS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
    "https://btc.calendar.catallaxy.com",
)
MIN_CALENDARS = 2
OTS_HEADERS = {
    "Accept": "application/vnd.opentimestamps.v1",
    "User-Agent": "lucky2049-anchor/1.0",
}


def _varuint(value):
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _varbytes(data):
    return _varuint(len(data)) + data


def _atomic_write_bytes(directory, filename, payload):
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix="-" + filename)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, os.path.join(directory, filename))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_public_ip(ip):
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_global
        and not (ip.is_private or ip.is_loopback or ip.is_link_local
                 or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
    )


def _check_safe_calendar_url(url, resolve_dns=False):
    """Ensure calendar URLs (especially untrusted PendingAttestation URIs) use HTTPS
    on a public hostname, never userinfo/queries/localhost or non-global IP space."""
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError(f"unsafe calendar URI: {url!r}")
    try:
        port = parts.port or 443
    except ValueError as e:
        raise ValueError(f"invalid calendar port in {url!r}: {e}") from e
    host = parts.hostname.rstrip(".").lower()
    if (not host or host in ("localhost", "localhost.localdomain")
            or host.endswith((".localhost", ".local", ".localdomain", ".internal"))):
        raise ValueError(f"disallowed local calendar host: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not _is_public_ip(ip):
            raise ValueError(f"disallowed non-public calendar IP: {host!r}")
        return
    if resolve_dns:
        try:
            addrs = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"unresolvable calendar host {host!r}: {e}") from e
        if not addrs:
            raise ValueError(f"no DNS addresses for calendar host: {host!r}")
        for _, _, _, _, sockaddr in addrs:
            resolved = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
            if not _is_public_ip(resolved):
                raise ValueError(f"disallowed non-public calendar IP {resolved} for {host!r}")


def _default_post_digest(cal_url, digest32):
    _check_safe_calendar_url(cal_url, resolve_dns=True)
    url = cal_url.rstrip("/") + "/digest"
    headers = {**OTS_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
    return verify._http_request_bytes(
        url, method="POST", data=digest32, headers=headers, timeout=15, retries=2,
        allow_redirects=False, max_bytes=4096,
    )


def _default_get_timestamp(cal_url, commit_msg):
    _check_safe_calendar_url(cal_url, resolve_dns=True)
    url = cal_url.rstrip("/") + "/timestamp/" + commit_msg.hex()
    try:
        return verify._http_request_bytes(
            url, method="GET", headers=OTS_HEADERS, timeout=15, retries=2,
            allow_redirects=False, max_bytes=8192,
        )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # calendar commitment not yet anchored in a Bitcoin block
        raise


def stamp_bytes(data_bytes, calendars=DEFAULT_CALENDARS, min_calendars=MIN_CALENDARS, post_digest=None):
    """Stamp `data_bytes` across multiple OpenTimestamps calendars and return a complete
    detached .ots proof. Requires >= min_calendars valid responses."""
    send = post_digest or _default_post_digest
    file_digest = hashlib.sha256(data_bytes).digest()
    nonce = os.urandom(16)
    salted = hashlib.sha256(file_digest + nonce).digest()

    top_items = []
    ok_cals = []
    errors = []
    for cal in calendars:
        try:
            raw = send(cal, salted)
            if not isinstance(raw, (bytes, bytearray)) or not (1 <= len(raw) <= 4096):
                raise ValueError(f"unexpected response size ({len(raw) if raw else 0}B)")
            items = accept_anchors.split_timestamp_items(bytes(raw))
            top_items.extend(items)
            ok_cals.append(cal)
        except Exception as e:
            errors.append(f"{cal}: {e}")

    if len(ok_cals) < min_calendars:
        raise RuntimeError(
            f"only {len(ok_cals)}/{min_calendars} OTS calendars succeeded ({'; '.join(errors)})"
        )

    merged = b"".join(b"\xff" + it for it in top_items[:-1]) + top_items[-1]
    ots = accept_anchors.OTS_MAGIC + b"\x01\x08" + file_digest + b"\xf0" + _varbytes(nonce) + b"\x08" + merged
    problem = accept_anchors.proof_problem(ots, data_bytes)
    if problem:
        raise RuntimeError(f"internal error assembling OTS proof: {problem}")
    return ots


def _splice_single(ots_bytes, was_ff, item_start, item_end, sub_ts, top_items):
    buf = bytearray(ots_bytes)
    pending_slice = bytes(buf[item_start:item_end])
    if was_ff:
        buf[item_start - 1:item_end] = b"\xff" + pending_slice + b"".join(b"\xff" + it for it in top_items)
    else:
        buf[item_start:item_end] = b"\xff" + pending_slice + sub_ts
    return bytes(buf)


def upgrade_proof_bytes(ots_bytes, get_timestamp=None):
    """Attempt to upgrade an unattested .ots proof by querying its pending calendars.
    Returns (new_ots_bytes, changed). Already-attested proofs return (ots_bytes, False)."""
    if accept_anchors.is_attested(ots_bytes):
        return ots_bytes, False
    fetch = get_timestamp or _default_get_timestamp
    _, _, pending_leaves = accept_anchors.walk_ots(ots_bytes, evaluate=True)
    splices = []
    for was_ff, item_start, item_end, uri, commit_msg in pending_leaves:
        try:
            sub_ts = fetch(uri, commit_msg)
            if not sub_ts:
                continue
            sub_ts = bytes(sub_ts)
            if len(sub_ts) > 8192:
                continue
            top_items = accept_anchors.split_timestamp_items(sub_ts)
            trial = _splice_single(ots_bytes, was_ff, item_start, item_end, sub_ts, top_items)
            if len(trial) > accept_anchors.MAX_OTS_BYTES or not accept_anchors.eval_bitcoin_attestations(trial):
                continue
            splices.append((was_ff, item_start, item_end, sub_ts, top_items))
        except Exception as e:
            print(f"warning: calendar upgrade skipped for {uri}: {e}")

    if not splices:
        return ots_bytes, False

    buf = bytearray(ots_bytes)
    for was_ff, item_start, item_end, sub_ts, top_items in reversed(splices):
        candidate = bytearray(buf)
        pending_slice = bytes(candidate[item_start:item_end])
        if was_ff:
            candidate[item_start - 1:item_end] = b"\xff" + pending_slice + b"".join(b"\xff" + it for it in top_items)
        else:
            candidate[item_start:item_end] = b"\xff" + pending_slice + sub_ts
        if len(candidate) <= accept_anchors.MAX_OTS_BYTES:
            buf = candidate

    upgraded = bytes(buf)
    if upgraded == ots_bytes or not accept_anchors.eval_bitcoin_attestations(upgraded):
        return ots_bytes, False
    return upgraded, True


def stamp_anchors_dir(anchors_dir, calendars=DEFAULT_CALENDARS, min_calendars=MIN_CALENDARS, post_digest=None):
    """Stamp every NNNNNN.head.json in `anchors_dir` that lacks a companion .ots file."""
    stamped = 0
    for name in sorted(os.listdir(anchors_dir)):
        if not (accept_anchors.NAME_RE.fullmatch(name) and name.endswith(".json")):
            continue
        ots_name = name + ".ots"
        if os.path.exists(os.path.join(anchors_dir, ots_name)):
            continue
        with open(os.path.join(anchors_dir, name), "rb") as f:
            head_bytes = f.read()
        ots = stamp_bytes(head_bytes, calendars=calendars, min_calendars=min_calendars, post_digest=post_digest)
        _atomic_write_bytes(anchors_dir, ots_name, ots)
        stamped += 1
        print(f"stamped {ots_name} ({len(ots)} bytes)")
    return stamped


def upgrade_anchors_dir(anchors_dir, get_timestamp=None):
    """Upgrade ONLY unattested .ots proofs in `anchors_dir`. Already-attested proofs
    are skipped immediately so lagging calendars never mutate finalized files."""
    upgraded_count = 0
    for name in sorted(os.listdir(anchors_dir)):
        if not (accept_anchors.NAME_RE.fullmatch(name) and name.endswith(".ots")):
            continue
        path = os.path.join(anchors_dir, name)
        with open(path, "rb") as f:
            ots_bytes = f.read()
        if accept_anchors.is_attested(ots_bytes):
            continue
        try:
            new_ots, changed = upgrade_proof_bytes(ots_bytes, get_timestamp=get_timestamp)
        except Exception as e:
            print(f"warning: could not upgrade {name}: {e}")
            continue
        if changed:
            _atomic_write_bytes(anchors_dir, name, new_ots)
            upgraded_count += 1
            print(f"upgraded {name} ({len(ots_bytes)} -> {len(new_ots)} bytes)")
    return upgraded_count


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


def write_current_head(base, anchors_dir):
    head = load_head(base)
    draw_id = head.get("draw_id")
    head_hex = head.get("head")
    if draw_id is None or draw_id < 0 or not head_hex:
        print(f"no head to anchor (head = {head})")
        return False

    os.makedirs(anchors_dir, exist_ok=True)
    name = f"{draw_id:06d}.head.json"
    path = os.path.join(anchors_dir, name)
    if os.path.exists(path):
        print(f"draw {draw_id} already anchored: {name}")
        return True

    record = {
        "head": head_hex,
        "draw_id": draw_id,
        "count": head.get("count"),
        "algo_version": head.get("algo_version", "v1"),
    }
    payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(anchors_dir, name, payload)
    print(f"wrote {name} (head {head_hex[:16]}...)")
    return True


def main(base=None, anchors_dir=None, stamp=False, upgrade=False, argv=None):
    if base is None and anchors_dir is None and not stamp and not upgrade:
        ap = argparse.ArgumentParser(description="Write, stamp, and upgrade OpenTimestamps head anchors (stdlib).")
        ap.add_argument("base", nargs="?", default=DEFAULT_BASE, help="site base URL or local directory")
        ap.add_argument("--anchors-dir", default=os.path.join(REPO, "anchors"))
        ap.add_argument("--stamp", action="store_true", help="stamp any .head.json lacking a .ots proof")
        ap.add_argument("--upgrade", action="store_true", help="upgrade unattested .ots proofs")
        args = ap.parse_args(argv)
        base, anchors_dir, stamp, upgrade = args.base, args.anchors_dir, args.stamp, args.upgrade
    else:
        base = DEFAULT_BASE if base is None else base
        anchors_dir = os.path.join(REPO, "anchors") if anchors_dir is None else anchors_dir

    has_anchors = write_current_head(base, anchors_dir)
    if not has_anchors and not os.path.isdir(anchors_dir):
        return 0
    if stamp and os.path.isdir(anchors_dir):
        stamp_anchors_dir(anchors_dir)
    if upgrade and os.path.isdir(anchors_dir):
        upgrade_anchors_dir(anchors_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
