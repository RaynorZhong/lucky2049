#!/usr/bin/env python3
"""
Tests for the pure-stdlib OpenTimestamps protocol implementation (Option 1B):
  1. Cryptographic Merkle-root evaluation against real committed proofs in anchors/.
  2. Multi-calendar pure-stdlib stamping (16-byte nonce, >=2 valid calendar responses).
  3. Idempotent pure-stdlib upgrading (skips already-attested proofs to prevent the
     multi-calendar upgrade deadlock; upgrades pending proofs and passes accept_anchors).
  4. On-chain Bitcoin block header merkle_root verification in accept_anchors.py.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import accept_anchors as A  # noqa: E402
import anchor_head as H  # noqa: E402


def _vb(b):
    out = bytearray()
    n = len(b)
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out) + b


def _varuint(v):
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def _pending(uri):
    return b"\x00" + A.PENDING_ATTESTATION + _vb(_vb(uri.encode("ascii")))


def _bitcoin(height):
    return b"\x00" + A.BITCOIN_ATTESTATION + _vb(_varuint(height))


class TestOtsMerkleEvaluation(unittest.TestCase):
    def test_evaluates_real_committed_anchor_006612_to_mainnet_merkle_roots(self):
        anchors = os.path.join(REPO_ROOT, "anchors")
        with open(os.path.join(anchors, "006612.head.json"), "rb") as f:
            head_bytes = f.read()
        with open(os.path.join(anchors, "006612.head.json.ots"), "rb") as f:
            ots_bytes = f.read()
        atts = A.eval_bitcoin_attestations(ots_bytes, head_bytes)
        self.assertIn(
            (952310, "f039bfee7273dd5a442943e13a46f22dbaff023a425697e74f28891923ea3671"),
            atts,
        )

    def test_rejects_non_32_byte_leaf_at_bitcoin_attestation(self):
        head_bytes = b'{"head":"aa"}\n'
        # append 4 bytes without hashing back to 32 bytes -> 36-byte leaf msg (invalid merkle root)
        bad_tree = b"\xf0" + _vb(b"abcd") + _bitcoin(850000)
        ots = A.OTS_MAGIC + b"\x01\x08" + hashlib.sha256(head_bytes).digest() + bad_tree
        with self.assertRaisesRegex(ValueError, "32 bytes"):
            A.eval_bitcoin_attestations(ots, head_bytes)

    @unittest.skipUnless(NODE, "node not available")
    def test_js_verify_ots_proof_matches_python_across_real_anchors(self):
        script = r"""
        const fs = require('fs');
        const V = require('./static/verify.js');
        const head = new Uint8Array(fs.readFileSync('./anchors/006612.head.json'));
        const ots = new Uint8Array(fs.readFileSync('./anchors/006612.head.json.ots'));
        const ok = V.verifyOtsProof(ots, head);
        const badDigest = V.verifyOtsProof(ots, new Uint8Array([1, 2, 3]));
        const badMagic = V.verifyOtsProof(new Uint8Array([0, 1, 2, 3]), head);
        console.log(JSON.stringify({ok, badDigest, badMagic}));
        """
        res = json.loads(subprocess.check_output([NODE, "-e", script], cwd=REPO_ROOT, text=True))
        self.assertTrue(res["ok"]["ok"])
        self.assertIn(
            {"height": 952310, "merkleRoot": "f039bfee7273dd5a442943e13a46f22dbaff023a425697e74f28891923ea3671"},
            res["ok"]["attestations"],
        )
        self.assertFalse(res["badDigest"]["ok"])
        self.assertFalse(res["badMagic"]["ok"])


class TestPureStdlibStampingAndUpgrading(unittest.TestCase):
    def test_stamp_bytes_aggregates_calendars_and_passes_accept_gate(self):
        head_bytes = A.canonical_head(10, "ab" * 32, "v1")
        seen_digests = []

        def fake_post(cal_url, digest32):
            seen_digests.append((cal_url, digest32))
            self.assertEqual(len(digest32), 32)
            if "bob" in cal_url:
                raise OSError("calendar timeout")
            # Calendar prepends 4B, appends 8B, then leaves a PendingAttestation
            return b"\xf1" + _vb(b"pre!") + b"\xf0" + _vb(b"post1234") + _pending(cal_url)

        cals = [
            "https://alice.btc.calendar.opentimestamps.org",
            "https://bob.btc.calendar.opentimestamps.org",
            "https://finney.calendar.eternitywall.com",
        ]
        ots = H.stamp_bytes(head_bytes, calendars=cals, min_calendars=2, post_digest=fake_post)
        self.assertIsNone(A.proof_problem(ots, head_bytes))
        self.assertFalse(A.is_attested(ots))
        # Both reachable calendars received the same salted 32-byte digest (not the raw file digest)
        self.assertEqual(seen_digests[0][1], seen_digests[2][1])
        self.assertNotEqual(seen_digests[0][1], hashlib.sha256(head_bytes).digest())

    def test_stamp_bytes_fails_when_quorum_not_reached(self):
        head_bytes = A.canonical_head(10, "ab" * 32, "v1")

        def mostly_down(cal_url, _d):
            if "alice" in cal_url:
                return _pending(cal_url)
            raise OSError("down")

        with self.assertRaisesRegex(RuntimeError, "calendar"):
            H.stamp_bytes(
                head_bytes,
                calendars=["https://alice.btc.calendar.opentimestamps.org", "https://bob.btc.calendar.opentimestamps.org"],
                min_calendars=2,
                post_digest=mostly_down,
            )

    def test_upgrade_proof_grafts_attestation_and_skips_already_attested_files(self):
        head_bytes = A.canonical_head(0, "cd" * 32, "v1")
        cal1 = "https://alice.btc.calendar.opentimestamps.org"
        cal2 = "https://finney.calendar.eternitywall.com"
        ots_pending = H.stamp_bytes(
            head_bytes,
            calendars=[cal1, cal2],
            min_calendars=2,
            post_digest=lambda cal, _d: b"\xf1" + _vb(b"1234") + b"\xf0" + _vb(b"56789012") + _pending(cal),
        )

        queried = []

        def fake_get_ts(uri, commit_msg):
            queried.append((uri, len(commit_msg)))
            # commit_msg is 4 + 32 + 8 = 44 bytes (just like real OTS calendars!)
            self.assertEqual(len(commit_msg), 44)
            if "finney" in uri:
                return None  # still pending on finney
            return b"\x08\xf0" + _vb(b"merkle-sibling") + b"\x08" + _bitcoin(955000)

        upgraded, changed = H.upgrade_proof_bytes(ots_pending, get_timestamp=fake_get_ts)
        self.assertTrue(changed)
        self.assertTrue(A.is_attested(upgraded))
        self.assertGreater(len(upgraded), len(ots_pending))
        self.assertIsNone(A.proof_problem(upgraded, head_bytes))
        atts = A.eval_bitcoin_attestations(upgraded, head_bytes)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0][0], 955000)

        # CRITICAL P1 REGRESSION LOCK: running upgrade_anchors_dir on a directory whose
        # .ots already has >=1 Bitcoin attestation MUST skip it without querying finney!
        with tempfile.TemporaryDirectory() as tmp:
            site = os.path.join(tmp, "site")
            incoming = os.path.join(tmp, "incoming")
            anchors = os.path.join(tmp, "anchors")
            for d in (site, incoming, anchors):
                os.makedirs(d)
            with open(os.path.join(site, "index.json"), "w") as f:
                f.write('{"draws":[{"id":0,"commitment":"' + ("cd" * 32) + '","algo_version":"v1"}],'
                        '"count":1,"head":{"head":"' + ("cd" * 32) + '","draw_id":0,"count":1,"algo_version":"v1"}}')
            # Seed anchors with pending, incoming with upgraded -> accept() admits it!
            for where, blob in ((anchors, ots_pending), (incoming, upgraded)):
                with open(os.path.join(where, "000000.head.json"), "wb") as f:
                    f.write(head_bytes)
                with open(os.path.join(where, "000000.head.json.ots"), "wb") as f:
                    f.write(blob)
            self.assertEqual(A.accept(incoming, anchors, site), ["000000.head.json.ots"])

            # Now run upgrade_anchors_dir on `anchors`: since 000000 is already attested,
            # it MUST NOT call get_timestamp at all (even though finney is still pending inside it)!
            queried.clear()
            upgraded_count = H.upgrade_anchors_dir(
                anchors,
                get_timestamp=lambda u, m: (_ for _ in ()).throw(AssertionError(f"touched attested proof: {u}")),
            )
            self.assertEqual(upgraded_count, 0)

    def test_accept_verify_chain_checks_merkle_root_quorum_and_rejects_forgery(self):
        head_bytes = A.canonical_head(0, "ef" * 32, "v1")
        # Build a valid attested proof at height 900123
        tree = b"\xf0" + _vb(b"salt123456789012") + b"\x08" + _bitcoin(900123)
        ots = A.OTS_MAGIC + b"\x01\x08" + hashlib.sha256(head_bytes).digest() + tree
        [(height, real_mr)] = A.eval_bitcoin_attestations(ots, head_bytes)
        self.assertEqual(height, 900123)

        with tempfile.TemporaryDirectory() as tmp:
            site, incoming, anchors = (os.path.join(tmp, x) for x in ("site", "incoming", "anchors"))
            for d in (site, incoming, anchors):
                os.makedirs(d)
            with open(os.path.join(site, "index.json"), "w") as f:
                f.write('{"draws":[{"id":0,"commitment":"' + ("ef" * 32) + '","algo_version":"v1"}],'
                        '"count":1,"head":{"head":"' + ("ef" * 32) + '","draw_id":0,"count":1,"algo_version":"v1"}}')
            with open(os.path.join(incoming, "000000.head.json"), "wb") as f:
                f.write(head_bytes)
            with open(os.path.join(incoming, "000000.head.json.ots"), "wb") as f:
                f.write(ots)

            # 1) Forged attestation (chain reports a DIFFERENT merkle_root at height 900123) -> Rejected!
            forged_fetcher = lambda h: {"mempool": "00" * 32, "blockstream": "00" * 32}  # noqa: E731
            with self.assertRaisesRegex(A.Rejected, "merkle_root"):
                A.accept(incoming, anchors, site, verify_chain=True, merkle_fetcher=forged_fetcher)
            self.assertEqual(os.listdir(anchors), [])

            # 2) Only 1 source reachable (< 2 quorum) -> Rejected!
            lone_fetcher = lambda h: {"mempool": real_mr}  # noqa: E731
            with self.assertRaisesRegex(A.Rejected, "quorum|sources"):
                A.accept(incoming, anchors, site, verify_chain=True, merkle_fetcher=lone_fetcher)

            # 3) Two independent sources confirm real_mr -> Accepted!
            honest_fetcher = lambda h: {"mempool": real_mr, "blockstream": real_mr}  # noqa: E731
            written = A.accept(incoming, anchors, site, verify_chain=True, merkle_fetcher=honest_fetcher)
            self.assertEqual(written, ["000000.head.json", "000000.head.json.ots"])
            # CLI --verify-chain path on unchanged directory is a clean exit 0
            self.assertEqual(A.main(["--verify-chain", incoming, anchors, site]), 0)

    def test_calendar_url_ssrf_guard_rejects_non_https_and_private_hosts(self):
        H._check_safe_calendar_url("https://alice.btc.calendar.opentimestamps.org")
        for bad in (
            "http://alice.btc.calendar.opentimestamps.org",
            "file:///etc/passwd",
            "https://localhost/digest",
            "https://localhost./digest",
            "https://sub.localhost/digest",
            "https://foo.local/digest",
            "https://metadata.google.internal/digest",
            "https://user:pass@alice.btc.calendar.opentimestamps.org/digest",
            "https://alice.btc.calendar.opentimestamps.org/digest?x=1",
            "https://127.0.0.1/digest",
            "https://10.0.0.1/digest",
            "https://100.64.0.1/digest",
            "https://169.254.169.254/latest/meta-data",
            "https://[::1]/digest",
            "https://[::ffff:127.0.0.1]/digest",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    H._check_safe_calendar_url(bad)
        # DNS-rebinding guard: hostname resolving to loopback / metadata IP is blocked
        with unittest.mock.patch.object(
            H.socket, "getaddrinfo",
            return_value=[(H.socket.AF_INET, H.socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "non-public calendar IP"):
                H._check_safe_calendar_url("https://rebind.example", resolve_dns=True)

    def test_cli_stamp_and_upgrade_end_to_end_and_direct_ff_branch_splice(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = os.path.join(tmp, "site")
            anchors = os.path.join(tmp, "anchors")
            os.makedirs(site)
            with open(os.path.join(site, "index.json"), "w") as f:
                json.dump({
                    "draws": [{"id": 0, "commitment": "11" * 32, "algo_version": "v1"}],
                    "count": 1,
                    "head": {"head": "11" * 32, "draw_id": 0, "count": 1, "algo_version": "v1"},
                }, f)

            # Exercise CLI main(argv=...) with --stamp and --upgrade using mocked calendar HTTP
            with unittest.mock.patch.object(
                H, "_default_post_digest",
                side_effect=lambda cal, _d: b"\xff" + _pending(cal) + b"\x08" + _pending(cal),
            ), unittest.mock.patch.object(
                H, "_default_get_timestamp",
                side_effect=lambda _u, _m: b"\xff" + (b"\x08" + _bitcoin(880001)) + (b"\xf2\x08" + _bitcoin(880002)),
            ):
                rc = H.main(argv=["--stamp", "--upgrade", "--anchors-dir", anchors, site])
            self.assertEqual(rc, 0)
            with open(os.path.join(anchors, "000000.head.json"), "rb") as f:
                hb = f.read()
            with open(os.path.join(anchors, "000000.head.json.ots"), "rb") as f:
                ots = f.read()
            self.assertTrue(A.is_attested(ots))
            heights = [h for h, _ in A.eval_bitcoin_attestations(ots, hb)]
            self.assertIn(880001, heights)
            self.assertIn(880002, heights)


if __name__ == "__main__":
    unittest.main()
