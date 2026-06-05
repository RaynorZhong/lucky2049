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
            # tip 100 -> confirmed 94; the next window (144..287) is not confirmed.
            rc = _run(idx, _agreeing(tip=100))
            self.assertEqual(rc, 0)
            self.assertEqual(_read(idx), before, "index.json must be untouched on a no-op")
            head = _read_json(os.path.join(tmp, "head.json"))
            self.assertEqual(head, {"head": "c0", "draw_id": 0, "count": 1, "algo_version": "v1"})

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

    def test_holds_when_too_few_sources(self):
        # Only one source reachable, but the default needs 2 to agree -> held.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            ha = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            rc = _run(idx, [_src("mempool", 200, ha)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_json(idx)["count"], 0)

    def test_min_agreement_one_allows_single_source(self):
        # An operator who trusts their own node can set MIN_SOURCE_AGREEMENT=1.
        with tempfile.TemporaryDirectory() as tmp:
            idx = _write_index(tmp, [])
            ha = lambda s, e: ["%064x" % h for h in range(s, e + 1)]
            with mock.patch.object(extend_pages, "MIN_AGREEMENT", 1):
                rc = _run(idx, [_src("mempool", 200, ha)])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_json(idx)["count"], 1)

    def test_publish_inputs_present(self):
        # The workflow copies exactly these into the published site; a rename that
        # silently drops one would 404 in production -- guard the list here.
        for rel in ("web/index.html", "web/verify.html", "web/stats.html", "web/trend.html",
                    "web/CNAME", "static/verify.js", "static/stats.js", "static/trend.js",
                    "static/style.css", "static/favicon.svg"):
            self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, rel)),
                            "missing publish input: " + rel)


if __name__ == "__main__":
    unittest.main()
