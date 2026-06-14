#!/usr/bin/env python3
"""
Tests the publisher/verifier robustness hardening:
  - verify._http_get retry/backoff (a transient 429/5xx no longer costs a vote),
  - extend_pages._safe_timestamp (a block-detail outage can't sink a good draw),
  - extend_pages reorg self-audit (a deep reorg altering a committed window ALARMS,
    never rewrites), and the tip-regression guard.
Stdlib only; all chain access is the fake _providers() seam -- no network.
"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import verify  # noqa: E402
import extend_pages  # noqa: E402

FAKE = lambda s, e: ["%064x" % h for h in range(s, e + 1)]  # noqa: E731


def _src(name, tip, hashes=None, ts="2026-01-01 00:00:00 UTC"):
    def _hashes(start, end):
        if hashes is None:
            raise AssertionError("fetched block hashes when none were expected")
        return hashes(start, end)

    def _ts(block_hash):
        if isinstance(ts, Exception):
            raise ts
        return ts
    return {"name": name, "tip": lambda: tip, "hashes": _hashes, "timestamp": _ts}


def _providers(tip, hashes=None, ts="2026-01-01 00:00:00 UTC", names=("mempool", "blockstream")):
    return [_src(n, tip, hashes, ts) for n in names]


def _write_index(tmp, draws):
    path = os.path.join(tmp, "index.json")
    with open(path, "w") as f:
        json.dump({"draws": draws, "count": len(draws), "head": {}}, f)
    return path


def _run(idx_path, providers):
    with mock.patch.object(extend_pages, "_providers", return_value=providers), \
         mock.patch.object(sys, "argv", ["extend_pages.py", idx_path]):
        return extend_pages.main()


def _status(tmp):
    with open(os.path.join(tmp, "status.json")) as f:
        return json.load(f)


def _draw(idx, end, front, back):
    return {"id": idx, "start_height": end - 143, "end_height": end, "front": front,
            "back": back, "algo_version": "v1", "commitment": "c%d" % idx,
            "prev_commitment": verify.GENESIS_PREV}


# ----------------------------- _http_get retry -----------------------------
class TestHttpRetry(unittest.TestCase):
    def test_retries_transient_then_succeeds(self):
        seq = [urllib.error.HTTPError("u", 429, "rate", {}, None),
               urllib.error.HTTPError("u", 503, "busy", {}, None),
               io.BytesIO(b"deadbeef")]

        def fake(req, timeout=None):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
             mock.patch.object(verify.time, "sleep"):
            self.assertEqual(verify._http_get("http://x"), "deadbeef")

    def test_raises_after_exhausting_retries(self):
        def fake(req, timeout=None):
            raise urllib.error.HTTPError("u", 429, "rate", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
             mock.patch.object(verify.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                verify._http_get("http://x", retries=3)

    def test_does_not_retry_non_transient(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError("u", 404, "not found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
             mock.patch.object(verify.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                verify._http_get("http://x")
        self.assertEqual(len(calls), 1)  # 404 is final -- no retry

    def test_retry_after_parsing(self):
        def err(headers):
            return urllib.error.HTTPError("u", 503, "busy", headers, None)
        self.assertEqual(verify._retry_after(err({"Retry-After": "3"})), 3.0)
        self.assertEqual(verify._retry_after(err({"Retry-After": "999"})), 10.0)   # capped
        self.assertIsNone(verify._retry_after(err({})))                            # absent
        self.assertIsNone(verify._retry_after(err({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})))  # HTTP-date form


# ----------------------------- _rpc_call retry -----------------------------
class TestRpcRetry(unittest.TestCase):
    RPC = "http://u:p@127.0.0.1:8332/"

    def test_rpc_retries_transient_then_succeeds(self):
        # The Core node is an agreement voter across a 144-call window; a transient
        # 5xx mid-window must be retried away, not drop its whole vote.
        seq = [urllib.error.HTTPError("u", 503, "busy", {}, None),
               urllib.error.HTTPError("u", 502, "bad gw", {}, None),
               io.BytesIO(b'{"result":"ab"}')]

        def fake(req, timeout=None):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
             mock.patch.object(verify.time, "sleep"):
            self.assertEqual(verify._rpc_call(self.RPC, "getblockcount", []), "ab")

    def test_rpc_does_not_retry_a_json_level_error(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            return io.BytesIO(b'{"error":{"code":-8,"message":"bad"}}')
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
             mock.patch.object(verify.time, "sleep"):
            with self.assertRaises(RuntimeError):
                verify._rpc_call(self.RPC, "getblockhash", [99])
        self.assertEqual(len(calls), 1)  # an RPC-level error is final, not retried


# ----------------------------- timestamp fallback -----------------------------
class TestSafeTimestamp(unittest.TestCase):
    def test_timestamp_outage_commits_draw_with_null_ts(self):
        # Hashes agree (sources reachable) but the block-detail/timestamp op fails
        # on every source: the draw must still commit, with timestamp = null.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            provs = _providers(tip=200, hashes=FAKE, ts=RuntimeError("block detail down"))
            self.assertEqual(_run(idx, provs), 0)
            draws = json.load(open(idx))["draws"]
            self.assertEqual(len(draws), 1)             # draw 0 committed despite the outage
            self.assertIsNone(draws[0]["timestamp"])    # cosmetic field falls back to null


# ----------------------------- reorg self-audit -----------------------------
class TestReorgAudit(unittest.TestCase):
    def _recent_draw0(self):
        f, b, _ = verify.generate(FAKE(0, 143))
        return f, b

    def test_audit_match_is_silent(self):
        f, b = self._recent_draw0()
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [_draw(0, 143, f, b)])
            # tip 160 -> confirmed 154; draw 0 depth 11 < 36 -> audited; chain agrees.
            self.assertEqual(_run(idx, _providers(tip=160, hashes=FAKE)), 0)
            st = _status(tmp)
            self.assertEqual(st["added"], 0)
            self.assertNotIn("note", st)                # result still matches -> no alarm

    def test_audit_mismatch_alarms_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Stored result is deliberately WRONG vs what the chain (FAKE) yields.
            idx = _write_index(tmp, [_draw(0, 143, [1, 2, 3, 4, 5], [1, 2])])
            self.assertEqual(_run(idx, _providers(tip=160, hashes=FAKE)), 0)
            st = _status(tmp)
            self.assertIn("note", st)
            self.assertIn("REORG/MISMATCH", st["note"])  # alarms via note
            kept = json.load(open(idx))["draws"][0]
            self.assertEqual((kept["front"], kept["back"]), ([1, 2, 3, 4, 5], [1, 2]))  # never rewritten

    def test_buried_draw_is_not_refetched(self):
        f, b = self._recent_draw0()
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [_draw(0, 143, f, b)])
            # tip 200 -> confirmed 194: the audit block RUNS (added==0, quorum, draws),
            # but draw 0's depth 194-143=51 >= REORG_AUDIT_DEPTH(36), so _reorg_audit_one
            # must return early WITHOUT re-fetching. hashes=None => any fetch raises, so
            # this directly covers the buried-skip branch (not the added>0 skip).
            self.assertEqual(_run(idx, _providers(tip=200, hashes=None)), 0)
            st = _status(tmp)
            self.assertEqual(st["added"], 0)
            self.assertNotIn("note", st)


# ----------------------------- tip regression guard -----------------------------
class TestTipRegressionGuard(unittest.TestCase):
    def test_tip_below_last_window_holds_and_alarms(self):
        f, b = verify.generate(FAKE(0, 143))[0], verify.generate(FAKE(0, 143))[1]
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [_draw(0, 143, f, b)])
            before = open(idx).read()
            # Two sources agree on tip 100, below the last committed window (end 143):
            # impossible for a real chain -> no draw, alarm note, index untouched.
            self.assertEqual(_run(idx, _providers(tip=100, hashes=None)), 0)
            self.assertEqual(open(idx).read(), before)
            st = _status(tmp)
            self.assertIn("note", st)
            self.assertIn("regressed", st["note"])
            self.assertEqual(st["added"], 0)


if __name__ == "__main__":
    unittest.main()
