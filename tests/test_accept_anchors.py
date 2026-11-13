#!/usr/bin/env python3
"""
Locks scripts/accept_anchors.py -- the gate between the anchor workflow's two jobs.

The `stamp` job runs third-party code (the OpenTimestamps client from PyPI) and is
therefore treated as UNTRUSTED: it has a read-only token and hands its output over
as an artifact. The `commit` job holds the write token and runs no third-party code;
before committing it passes the artifact through this script, which accepts only
well-formed anchor files that say what the published history says. These tests pin
the rules, because each is the only thing standing between a compromised dependency
and a commit on main. Stdlib only.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import accept_anchors as A  # noqa: E402


def _vb(b):                       # varbytes with a 1-byte length (all test payloads are short)
    assert len(b) < 0x80
    return bytes([len(b)]) + b


def _pending(uri=b"https://alice.btc.calendar.example"):
    return b"\x00" + A.PENDING_ATTESTATION + _vb(_vb(uri))


def _bitcoin(height=100):
    return b"\x00" + A.BITCOIN_ATTESTATION + _vb(bytes([height]))


PENDING_TREE = b"\xf0" + _vb(b"nonce") + b"\x08" + _pending()                       # append, sha256, pending
ATTESTED_TREE = b"\xf0" + _vb(b"nonce") + b"\x08" + b"\xff" + _pending() + b"\xf1" + _vb(b"path") + b"\x08" + _bitcoin()


def _commit(i):
    return hashlib.sha256(b"draw %d" % i).hexdigest()


def _index(n):
    draws = [{"id": i, "commitment": _commit(i), "algo_version": "v1"} for i in range(n)]
    return {"draws": draws, "count": n,
            "head": {"head": _commit(n - 1), "draw_id": n - 1, "count": n, "algo_version": "v1"}}


def _head_bytes(i):
    rec = {"head": _commit(i), "draw_id": i, "count": i + 1, "algo_version": "v1"}
    return (json.dumps(rec, indent=2, sort_keys=True) + "\n").encode()


def _ots(head_bytes, tree=PENDING_TREE):
    return A.OTS_MAGIC + b"\x01\x08" + hashlib.sha256(head_bytes).digest() + tree


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        t = self._tmp.name
        self.site, self.incoming, self.anchors = (os.path.join(t, d) for d in ("site", "incoming", "anchors"))
        for d in (self.site, self.incoming, self.anchors):
            os.makedirs(d)
        self.write_index(_index(10))

    def write_index(self, index):
        with open(os.path.join(self.site, "index.json"), "w") as f:
            json.dump(index, f)

    def put(self, where, name, data):
        with open(os.path.join(where, name), "wb") as f:
            f.write(data)

    def pair(self, where, i, tree=PENDING_TREE):
        hb = _head_bytes(i)
        self.put(where, "%06d.head.json" % i, hb)
        self.put(where, "%06d.head.json.ots" % i, _ots(hb, tree))

    def run_accept(self):
        return A.accept(self.incoming, self.anchors, self.site, sleep=lambda s: None)

    def names(self):
        return sorted(os.listdir(self.anchors))

    def snapshot(self):
        return {n: open(os.path.join(self.anchors, n), "rb").read() for n in self.names()}


class TestProofParser(unittest.TestCase):
    def test_synthetic_trees_parse_and_report_attestation(self):
        hb = _head_bytes(1)
        self.assertEqual(A.parse_proof(_ots(hb)), (hashlib.sha256(hb).digest(), False))
        self.assertEqual(A.parse_proof(_ots(hb, ATTESTED_TREE)), (hashlib.sha256(hb).digest(), True))

    def test_malformed_proofs_are_refused(self):
        good = _ots(_head_bytes(1), ATTESTED_TREE)
        n = len(A.OTS_MAGIC)
        bad = {
            "magic": b"\x01" + good[1:],
            "version": good[:n] + b"\x02" + good[n + 1:],
            "hash op": good[:n + 1] + b"\x02" + good[n + 2:],
            "header only": good[:n + 34],
            "truncated": good[:-2],
            "trailing byte": good + b"\x00",
            "garbage body": good[:n + 34] + b"\x00" * 40,
            "tag embedded in junk": good[:n + 34] + A.BITCOIN_ATTESTATION + b"junk",
            "unknown op": good[:n + 34] + b"\x55" + _pending(),
            "unknown attestation": good[:n + 34] + b"\x00" + b"\xaa" * 8 + _vb(b"x"),
            "attestation payload too long": good[:n + 34] + b"\x00" + A.BITCOIN_ATTESTATION + _vb(b"\x05\x05"),
        }
        for why, blob in bad.items():
            self.assertIsNotNone(A.proof_problem(blob, _head_bytes(1)), why)
            self.assertFalse(A.is_attested(blob), why)

    def test_deep_hash_chains_do_not_hit_a_recursion_limit(self):
        tree = (b"\xf0" + _vb(b"x") + b"\x08") * 5000 + _bitcoin()
        self.assertTrue(A.parse_proof(_ots(_head_bytes(1), tree))[1])


class TestAccepts(_Case):
    def test_new_valid_pair_is_accepted(self):
        self.pair(self.incoming, 9)
        self.assertEqual(self.run_accept(), ["000009.head.json", "000009.head.json.ots"])
        self.assertEqual(self.names(), ["000009.head.json", "000009.head.json.ots"])
        self.assertEqual(self.snapshot()["000009.head.json"], _head_bytes(9))

    def test_unchanged_files_are_a_noop(self):
        self.pair(self.anchors, 8)
        self.pair(self.incoming, 8)
        self.assertEqual(self.run_accept(), [])

    def test_upgraded_proof_replaces_a_pending_one(self):
        self.pair(self.anchors, 8)                                        # pending on main
        self.pair(self.incoming, 8, ATTESTED_TREE)
        self.assertEqual(self.run_accept(), ["000008.head.json.ots"])
        self.assertTrue(A.is_attested(self.snapshot()["000008.head.json.ots"]))

    def test_a_different_pending_proof_is_ignored_not_swapped_in(self):
        self.pair(self.anchors, 8)
        before = self.snapshot()
        self.pair(self.incoming, 8, b"\x08" + _pending(b"https://evil.example"))
        self.assertEqual(self.run_accept(), [])
        self.assertEqual(self.snapshot(), before)                         # the real calendar commitments stay

    def test_a_stale_published_index_is_retried_before_rejecting(self):
        self.pair(self.incoming, 9)
        calls = []
        real = A.load_draws

        def flaky(base):
            calls.append(base)
            draws = real(self.site)                                      # never touches the network
            return draws[:9] if len(calls) < 3 else draws                # draw 9 "appears" on the 3rd read
        slept = []
        orig = A.load_draws
        A.load_draws = flaky
        try:
            got = A.accept(self.incoming, self.anchors, "https://example.invalid", sleep=slept.append)
        finally:
            A.load_draws = orig
        self.assertEqual(got, ["000009.head.json", "000009.head.json.ots"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(slept, [A.PUBLISHED_RETRY_SLEEP_S] * 2)


class TestRejects(_Case):
    def assertRejected(self, regex):
        before = self.snapshot()
        with self.assertRaisesRegex(A.Rejected, regex):
            self.run_accept()
        self.assertEqual(before, self.snapshot(), "a rejected batch must change nothing")

    def test_head_that_is_not_the_published_head_is_rejected(self):
        hb = _head_bytes(9).replace(_commit(9).encode(), _commit(3).encode())   # right id, wrong commitment
        self.put(self.incoming, "000009.head.json", hb)
        self.put(self.incoming, "000009.head.json.ots", _ots(hb))
        self.assertRejected("does not match the published history")

    def test_head_for_a_draw_that_does_not_exist_is_rejected(self):
        self.pair(self.incoming, 42)
        self.assertRejected("not a published draw")

    def test_index_whose_ids_do_not_match_positions_is_rejected(self):
        idx = _index(10)
        idx["draws"][9]["id"] = 8
        self.write_index(idx)
        self.pair(self.incoming, 9)
        self.assertRejected("not a published draw")

    def test_non_canonical_head_bytes_are_rejected(self):
        hb = json.dumps(json.loads(_head_bytes(9))).encode()                    # same content, different bytes
        self.put(self.incoming, "000009.head.json", hb)
        self.put(self.incoming, "000009.head.json.ots", _ots(hb))
        self.assertRejected("does not match the published history")

    def test_existing_head_can_never_be_modified(self):
        self.pair(self.anchors, 8)
        self.put(self.incoming, "000008.head.json", _head_bytes(8) + b" ")
        self.put(self.incoming, "000008.head.json.ots", _ots(_head_bytes(8)))
        self.assertRejected("existing anchor .* would be modified")

    def test_only_one_new_head_per_batch_and_it_must_be_the_newest(self):
        self.pair(self.anchors, 7)
        self.pair(self.incoming, 8)
        self.pair(self.incoming, 9)
        self.assertRejected("2 new heads")
        for n in ("000008.head.json", "000008.head.json.ots", "000009.head.json", "000009.head.json.ots"):
            os.remove(os.path.join(self.incoming, n))
        self.pair(self.incoming, 3)                 # a real, published, never-anchored OLD draw
        self.assertRejected("older than the newest anchored head")

    def test_proof_for_a_different_file_is_rejected(self):
        self.put(self.incoming, "000009.head.json", _head_bytes(9))
        self.put(self.incoming, "000009.head.json.ots", _ots(_head_bytes(8)))   # digest of another file
        self.assertRejected("digest")

    def test_proof_that_is_not_a_well_formed_ots_file_is_rejected(self):
        self.put(self.incoming, "000009.head.json", _head_bytes(9))
        good = _ots(_head_bytes(9))
        n = len(A.OTS_MAGIC)
        for blob in (b"#!/bin/sh\nrm -rf /\n", good[:n + 34] + b"\x00" * 40,
                     good[:n + 34] + A.BITCOIN_ATTESTATION + b"junk", good + b"\x00"):
            self.put(self.incoming, "000009.head.json.ots", blob)
            self.assertRejected("not an OpenTimestamps proof")

    def test_an_attested_proof_is_immutable(self):
        self.pair(self.anchors, 8, ATTESTED_TREE)
        self.pair(self.incoming, 8)                                              # back to pending
        self.assertRejected("already carries a Bitcoin attestation")
        bigger = ATTESTED_TREE[:-len(_bitcoin())] + b"\xff" + _bitcoin(7) + b"\x08" + _bitcoin(9)
        self.pair(self.incoming, 8, bigger)                                      # even a "richer" one
        self.assertRejected("already carries a Bitcoin attestation")

    def test_an_upgrade_may_not_shrink_the_proof(self):
        self.pair(self.anchors, 8, b"\xf0" + _vb(b"a" * 100) + b"\x08" + _pending())
        self.pair(self.incoming, 8, _bitcoin())                                  # attested but much smaller
        self.assertRejected("would shrink")

    def test_new_head_without_a_proof_is_rejected(self):
        self.put(self.incoming, "000009.head.json", _head_bytes(9))
        self.assertRejected("has no proof")

    def test_proof_without_any_head_is_rejected(self):
        self.put(self.incoming, "000009.head.json.ots", _ots(_head_bytes(9)))
        self.assertRejected("has no head file")

    def test_unexpected_names_are_rejected(self):
        self.pair(self.incoming, 9)
        for name in ("evil.py", "..head.json", "9.head.json", "000009.head.json.ots.bak", "README.md",
                     "000009.head.json\n", "٠٠٠٠٠٩.head.json"):   # newline / Arabic-Indic digits
            self.put(self.incoming, name, b"x")
            self.assertRejected("unexpected file")
            os.remove(os.path.join(self.incoming, name))
        os.makedirs(os.path.join(self.incoming, "sub"))
        self.assertRejected("unexpected")

    def test_special_and_oversized_files_are_rejected(self):
        self.assertEqual((A.MAX_HEAD_BYTES, A.MAX_OTS_BYTES, A.MAX_NEW_HEADS), (1024, 16 * 1024, 1))
        self.pair(self.incoming, 9)
        os.symlink("/etc/passwd", os.path.join(self.incoming, "000007.head.json"))
        self.assertRejected("not a regular file")
        os.remove(os.path.join(self.incoming, "000007.head.json"))
        self.put(self.incoming, "000009.head.json.ots", b"\x00" * (A.MAX_OTS_BYTES + 1))
        self.assertRejected("too large")
        self.pair(self.incoming, 9)
        self.put(self.incoming, "000009.head.json", b" " * (A.MAX_HEAD_BYTES + 1))
        self.assertRejected("too large")

    def test_one_bad_file_rejects_the_whole_batch(self):
        self.pair(self.anchors, 7)
        self.pair(self.incoming, 7, ATTESTED_TREE)        # a perfectly good upgrade ...
        self.pair(self.incoming, 9)
        self.put(self.incoming, "000009.head.json.ots", b"garbage")   # ... next to one bad proof
        self.assertRejected("not an OpenTimestamps proof")


class TestPendingCheck(_Case):
    def test_recent_pending_proofs_are_fine_old_ones_are_flagged(self):
        for i in (5, 6, 7, 8, 9):
            self.pair(self.anchors, i, PENDING_TREE if i >= 6 else ATTESTED_TREE)
        # 6 is exactly max_behind (3) draws behind the newest (9): still tolerated
        self.assertEqual(A.stale_pending(self.anchors, max_behind=3), [])
        self.pair(self.anchors, 5)                                               # 4 behind and pending
        self.assertEqual(A.stale_pending(self.anchors, max_behind=3), ["000005.head.json.ots"])
        self.assertEqual(A.MAX_PENDING_BEHIND, 3)

    def test_a_proof_that_no_longer_parses_counts_as_not_attested(self):
        for i in (1, 9):
            self.pair(self.anchors, i, ATTESTED_TREE)
        self.put(self.anchors, "000001.head.json.ots", A.OTS_MAGIC + A.BITCOIN_ATTESTATION + b"junk")
        self.assertEqual(A.stale_pending(self.anchors), ["000001.head.json.ots"])

    def test_cli_exit_codes(self):
        self.pair(self.incoming, 9)
        self.assertEqual(A.main([self.incoming, self.anchors, self.site]), 0)
        self.put(self.incoming, "evil.py", b"x")
        self.assertEqual(A.main([self.incoming, self.anchors, self.site]), 1)
        self.assertEqual(A.main(["--check-pending", self.anchors]), 0)
        self.pair(self.anchors, 2)
        self.assertEqual(A.main(["--check-pending", self.anchors]), 1)


class TestRealAnchors(unittest.TestCase):
    """The rules must hold for everything already committed -- including every older
    version of every proof in git history (the honest pending -> attested upgrades)."""

    def test_every_committed_proof_is_a_well_formed_proof_of_its_head(self):
        d = os.path.join(REPO_ROOT, "anchors")
        heads = sorted(n for n in os.listdir(d) if A.NAME_RE.fullmatch(n) and n.endswith(".json"))
        self.assertGreater(len(heads), 100)
        for n in heads:
            with open(os.path.join(d, n), "rb") as f:
                hb = f.read()
            with open(os.path.join(d, n + ".ots"), "rb") as f:
                self.assertIsNone(A.proof_problem(f.read(), hb), n)
        self.assertEqual(A.stale_pending(d), [])

    def test_history_of_proofs_obeys_the_upgrade_rules(self):
        git = ["git", "-C", REPO_ROOT]
        try:
            log = subprocess.run(git + ["log", "--format=%H", "--reverse", "-60", "--", "anchors"],
                                 capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git history not available")
        if len(log) < 5:
            self.skipTest("shallow clone: not enough anchor history")
        last, upgrades = {}, 0
        for rev in log:
            tree = subprocess.run(git + ["ls-tree", "-r", rev, "anchors"], capture_output=True, text=True).stdout
            for line in tree.splitlines():
                meta, path = line.split("\t")
                blob = meta.split()[2]
                if not path.endswith(".ots") or last.get(path, (None,))[0] == blob:
                    continue
                data = subprocess.run(git + ["cat-file", "blob", blob], capture_output=True).stdout
                A.parse_proof(data)                                   # every version ever committed parses
                if path in last:
                    old = last[path][1]
                    self.assertFalse(A.is_attested(old), path + " changed after attestation")
                    self.assertTrue(A.is_attested(data), path + " changed without gaining an attestation")
                    self.assertGreaterEqual(len(data), len(old), path)
                    upgrades += 1
                last[path] = (blob, data)
        self.assertGreater(upgrades, 10)


if __name__ == "__main__":
    unittest.main()
