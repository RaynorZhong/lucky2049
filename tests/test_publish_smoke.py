#!/usr/bin/env python3
"""
Smoke-tests the static publish path (scripts/extend_pages.py) end-to-end without
touching the network. The publish step is exactly what the golden-vector unit
tests do NOT cover, and the cron silently stops updating the site if it breaks.

All chain access goes through extend_pages._providers(), so we patch that one
seam with a fake source -- no mempool / blockstream / Core calls. Stdlib only.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import verify  # noqa: E402  (frozen engine, stdlib-only)
import extend_pages  # noqa: E402


def _src(name, tip, hashes=None):
    """One provider. `hashes` is a callable (start, end) -> 144-hash list, or None
    to assert it is never fetched (the no-op path)."""
    def _hashes(start, end):
        if hashes is None:
            raise AssertionError("fetched block hashes on a no-op refresh")
        return hashes(start, end)
    return {"name": name, "tip": lambda: tip, "hashes": _hashes,
            "timestamp": lambda block_hash: "2026-01-01 00:00:00 UTC"}


def _agreeing(tip, hashes=None, names=("mempool", "blockstream")):
    """Several providers that share a tip and return the SAME hashes (they agree)."""
    return [_src(n, tip, hashes) for n in names]


def _write_index(tmp, draws):
    path = os.path.join(tmp, "index.json")
    with open(path, "w") as f:
        json.dump({"draws": draws, "count": len(draws), "head": {}}, f)
    return path


def _read(path):
    with open(path) as f:
        return f.read()


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _run(idx_path, providers):
    with mock.patch.object(extend_pages, "_providers", return_value=providers), \
         mock.patch.object(sys, "argv", ["extend_pages.py", idx_path]):
        return extend_pages.main()


class TestPublishSmoke(unittest.TestCase):
    def test_noop_writes_head_and_keeps_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            draw0 = {"id": 0, "start_height": 0, "end_height": 143,
                     "front": [1, 2, 3, 4, 5], "back": [1, 2], "algo_version": "v1",
                     "commitment": "c0", "prev_commitment": verify.GENESIS_PREV}
            idx = _write_index(tmp, [draw0])
            before = _read(idx)
            # tip 200 -> confirmed 194; the next window (144..287) is not confirmed,
            # and draw 0 is already buried > REORG_AUDIT_DEPTH so the reorg audit
            # doesn't re-fetch -- a genuine no-op that touches no source hashes.
            rc = _run(idx, _agreeing(tip=200))
            self.assertEqual(rc, 0)
            self.assertEqual(_read(idx), before, "index.json must be untouched on a no-op")
            head = _read_json(os.path.join(tmp, "head.json"))
            self.assertEqual(head, {"head": "c0", "draw_id": 0, "count": 1, "algo_version": "v1"})

            # status.json records every source's probe, even on a no-op
            st = _read_json(os.path.join(tmp, "status.json"))
            self.assertEqual([s["name"] for s in st["sources"]], ["mempool", "blockstream"])
            self.assertTrue(all(s["ok"] and s["tip"] == 200 for s in st["sources"]))
            self.assertEqual((st["added"], st["tip_source"]), (0, "mempool"))
            self.assertNotIn("held", st)
            self.assertNotIn("note", st)   # clean no-op must not raise any anomaly note

    def test_appends_confirmed_draw(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])  # empty -> first draw is id 0, window 0..143
            fake_hashes = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            # tip 200 -> confirmed 194: window 0..143 confirmed, 144..287 not.
            # two sources return the same hashes -> they agree -> committed.
            rc = _run(idx, _agreeing(tip=200, hashes=fake_hashes))
            self.assertEqual(rc, 0)

            data = _read_json(idx)
            self.assertEqual(data["count"], 1)
            rec = data["draws"][0]
            self.assertEqual(rec["id"], 0)
            self.assertEqual((rec["start_height"], rec["end_height"]), (0, 143))
            self.assertEqual(rec["prev_commitment"], verify.GENESIS_PREV)
            self.assertEqual(len(rec["front"]), verify.BLUE_BALL_NUM)
            self.assertEqual(len(rec["back"]), verify.RED_BALL_NUM)
            self.assertEqual(rec["timestamp"], "2026-01-01 00:00:00 UTC")

            # the stored commitment chains from genesis and matches a clean recompute
            front, back, seed = verify.generate(fake_hashes(0, 143))
            expect = verify.commitment_for(verify.GENESIS_PREV, 0, "v1", seed, front, back, 0, 143)
            self.assertEqual(rec["commitment"], expect)
            self.assertEqual(rec["front"], front)
            self.assertEqual(rec["back"], back)

            head = _read_json(os.path.join(tmp, "head.json"))
            self.assertEqual(head, {"head": expect, "draw_id": 0, "count": 1, "algo_version": "v1"})

            st = _read_json(os.path.join(tmp, "status.json"))
            self.assertEqual(st["added"], 1)

            # downstream beacon artifacts are written alongside index.json
            latest = _read_json(os.path.join(tmp, "latest.json"))
            self.assertEqual(latest["latest"]["id"], 0)
            self.assertEqual(latest["latest"]["verify_url"], "https://lucky2049.com/verify.html?draw=0")
            self.assertEqual(latest["head"]["draw_id"], 0)
            feed = _read_json(os.path.join(tmp, "feed.json"))
            self.assertIn("jsonfeed.org", feed["version"])
            self.assertEqual(feed["items"][0]["id"], "0")

    def test_holds_on_disagreement(self):
        # Two sources return DIFFERENT hashes for the window -> the draw is held,
        # nothing is committed to the irreversible chain, and the run still exits 0.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            before = _read(idx)
            ha = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            hb = lambda s, e: ["%064x" % (h + 7) for h in range(s, e + 1)]  # forked / wrong
            rc = _run(idx, [_src("mempool", 200, ha), _src("blockstream", 200, hb)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read(idx), before, "a disputed draw must NOT be committed")
            self.assertEqual(_read_json(idx)["count"], 0)
            self.assertEqual(_read_json(os.path.join(tmp, "head.json"))["draw_id"], -1)
            st = _read_json(os.path.join(tmp, "status.json"))
            self.assertEqual((st["added"], st["held"]), (0, 0), "the held draw must be visible in status.json")

    def test_holds_when_too_few_sources(self):
        # Only one source reachable, but the default needs 2 to agree -> held.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            ha = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            rc = _run(idx, [_src("mempool", 200, ha)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_json(idx)["count"], 0)

    def test_all_sources_down_publishes_red_status(self):
        # Every source failing must NOT crash the run: the draw loop is skipped,
        # nothing is committed, and an all-red status.json is still written so
        # the outage goes live instead of the site serving a stale green one.
        with tempfile.TemporaryDirectory() as tmp:
            draw0 = {"id": 0, "start_height": 0, "end_height": 143,
                     "front": [1, 2, 3, 4, 5], "back": [1, 2], "algo_version": "v1",
                     "commitment": "c0", "prev_commitment": verify.GENESIS_PREV}
            idx = _write_index(tmp, [draw0])
            before = _read(idx)

            def boom():
                raise RuntimeError("connection refused")
            srcs = [{"name": n, "tip": boom, "hashes": None, "timestamp": None}
                    for n in ("mempool", "blockstream")]
            rc = _run(idx, srcs)
            self.assertEqual(rc, 0, "a total outage must not crash the publisher")
            self.assertEqual(_read(idx), before)

            st = _read_json(os.path.join(tmp, "status.json"))
            self.assertEqual([s["ok"] for s in st["sources"]], [False, False])
            self.assertIn("connection refused", st["sources"][0]["error"])
            self.assertIsNone(st["tip_source"])
            self.assertEqual(st["added"], 0)
            # head.json still written (unchanged head), so the site stays whole
            self.assertEqual(_read_json(os.path.join(tmp, "head.json"))["draw_id"], 0)

    def test_min_agreement_one_allows_single_source(self):
        # An operator who trusts their own node can set MIN_SOURCE_AGREEMENT=1.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            ha = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            with mock.patch.object(extend_pages, "MIN_AGREEMENT", 1):
                rc = _run(idx, [_src("mempool", 200, ha)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_json(idx)["count"], 1)

    def test_inflated_single_source_tip_cannot_confirm_a_window(self):
        # One source reports an inflated tip; the quorum (MIN_AGREEMENT-th highest)
        # tip still gates maturity, so an under-confirmed draw is NOT committed --
        # a lone compromised/buggy tip source can't pull a window forward.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])  # draw 0 window 0..143 needs tip >= 149
            # core inflated to 200 (would confirm if it gated alone); mempool real at 100.
            # hashes=None asserts the hashes are never even fetched (no confirmed window).
            rc = _run(idx, [_src("core", 200), _src("mempool", 100)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_json(idx)["count"], 0, "inflated lone tip must not confirm a window")
            self.assertEqual(_read_json(os.path.join(tmp, "status.json"))["added"], 0)

    def test_quorum_tip_confirms_window(self):
        # When >= MIN_AGREEMENT sources agree the tip is high enough, it matures.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            fake_hashes = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            rc = _run(idx, [_src("core", 200, fake_hashes), _src("mempool", 200, fake_hashes)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_json(idx)["count"], 1)

    def test_one_healthy_source_holds_draw_even_with_high_tip(self):
        # A single healthy source (< MIN_AGREEMENT) never confirms a window, no
        # matter how high its tip -- depth needs a quorum just like the hashes.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            rc = _run(idx, [_src("core", 10_000)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_json(idx)["count"], 0)

    def test_chain_continuation_from_existing_draw(self):
        # The cron's real daily job: extend from the LAST published draw (N>0),
        # threading prev_commitment from its commitment. The empty-start tests never
        # exercise next_id/prev re-threading -- a regression there corrupts the chain.
        DRAW0 = "def4cd38f63acc6f39a9c4dbe0df021c00e219907cbfeb58752be198092b0739"  # golden
        with tempfile.TemporaryDirectory() as tmp:
            draw0 = {"id": 0, "start_height": 0, "end_height": 143,
                     "front": [11, 14, 19, 30, 35], "back": [2, 11], "algo_version": "v1",
                     "commitment": DRAW0, "prev_commitment": verify.GENESIS_PREV, "timestamp": "t"}
            idx = _write_index(tmp, [draw0])
            fake_hashes = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            # tip 500 -> confirmed 494: windows 144..287 (draw 1) and 288..431 (draw 2) mature.
            rc = _run(idx, _agreeing(tip=500, hashes=fake_hashes))
            self.assertEqual(rc, 0)
            draws = _read_json(idx)["draws"]
            self.assertEqual(len(draws), 3)
            prev = DRAW0
            for n in (1, 2):
                d = draws[n]
                self.assertEqual((d["id"], d["start_height"], d["end_height"]), (n, n * 144, n * 144 + 143))
                self.assertEqual(d["prev_commitment"], prev, f"draw {n} must chain from draw {n-1}")
                f, b, s = verify.generate(fake_hashes(n * 144, n * 144 + 143))
                self.assertEqual(d["commitment"],
                                 verify.commitment_for(prev, n, "v1", s, f, b, n * 144, n * 144 + 143))
                prev = d["commitment"]
            head = _read_json(os.path.join(tmp, "head.json"))
            self.assertEqual((head["draw_id"], head["head"], head["count"]), (2, prev, 3))

    def test_rpc_credentials_are_redacted_from_errors(self):
        # A BITCOIN_RPC_URL password must never reach the PUBLIC status.json / logs.
        msg = extend_pages._redact("urlopen error for https://bitcoinrpc:s3cret@btc-rpc.example/ : 403")
        self.assertNotIn("s3cret", msg)
        self.assertIn("https://***@btc-rpc.example/", msg)

    def test_redact_covers_bare_userinfo_and_cf_secret(self):
        # Userinfo with NO scheme (a bare host string) must still be stripped...
        bare = extend_pages._redact("error talking to bitcoinrpc:s3cret@btc-rpc.example : 403")
        self.assertNotIn("s3cret", bare)
        self.assertIn("***@btc-rpc.example", bare)
        # ...and the CF Access service-token secret (a header value, never userinfo)
        # is redacted by value when it leaks verbatim into an error string.
        with mock.patch.dict(os.environ, {"CF_ACCESS_CLIENT_SECRET": "cf-Sup3rSecret"}):
            cf = extend_pages._redact("403 Forbidden (token cf-Sup3rSecret rejected)")
        self.assertNotIn("cf-Sup3rSecret", cf)
        self.assertIn("***", cf)

    def test_redact_password_with_special_chars(self):
        # A BITCOIN_RPC_URL password containing '/' or '@' can't be spanned by the
        # userinfo regex; it must still be redacted by value.
        url = "http://user:p@ss/w0rd@btc-rpc.example:8332/"
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": url}):
            msg = extend_pages._redact("urlopen error for %s : 500" % url)
        self.assertNotIn("p@ss/w0rd", msg)   # the whole special-char password is gone

    def test_publish_inputs_present(self):
        # Parse the cron's own `cp web/... | static/... site/` lines and assert every
        # source it copies exists -- so a new published asset (or a typo) is guarded
        # automatically here instead of 404-ing in production, with no hand-kept list.
        wf = open(os.path.join(REPO_ROOT, ".github/workflows/refresh-pages.yml")).read()
        srcs = []
        for m in re.finditer(r"\bcp (?:-r )?((?:web|static)/[^\n]*?) site/", wf):
            srcs += [t for t in m.group(1).split() if t.startswith(("web/", "static/"))]
        self.assertTrue(srcs, "found no `cp web/|static/ ... site/` lines in refresh-pages.yml")
        for rel in srcs:
            self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, rel)), "publish input missing on disk: " + rel)
        for must in ("web/CNAME", "web/randomness.html", "static/randomness.js"):   # sanity anchors
            self.assertIn(must, srcs, "cron no longer copies " + must)


if __name__ == "__main__":
    unittest.main()
