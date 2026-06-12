#!/usr/bin/env python3
"""
Tests the daily tamper-evidence anchor writer (scripts/anchor_head.py). Its
failure mode is uniquely silent: a schema drift that makes load_head() return an
odd shape just prints "no head to anchor" and exits 0, so OpenTimestamps anchoring
quietly stops forever while the workflow stays green. These pin the contract.
Stdlib only; main() takes base/anchors_dir so nothing touches the real repo.
"""
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import anchor_head  # noqa: E402


def _site(tmp, index):
    with open(os.path.join(tmp, "index.json"), "w") as f:
        json.dump(index, f)
    return tmp


class TestAnchorHead(unittest.TestCase):
    def test_writes_anchor_for_current_head(self):
        head = {"head": "a" * 64, "draw_id": 6618, "count": 6619, "algo_version": "v1"}
        with tempfile.TemporaryDirectory() as site, tempfile.TemporaryDirectory() as anchors:
            _site(site, {"head": head, "draws": [], "count": 6619})
            self.assertEqual(anchor_head.main(base=site, anchors_dir=anchors), 0)
            rec = json.load(open(os.path.join(anchors, "006618.head.json")))
            self.assertEqual(rec, head)  # exactly head/draw_id/count/algo_version, zero-padded filename

    def test_skips_already_anchored(self):
        with tempfile.TemporaryDirectory() as site, tempfile.TemporaryDirectory() as anchors:
            _site(site, {"head": {"head": "a" * 64, "draw_id": 5, "count": 6, "algo_version": "v1"}})
            already = os.path.join(anchors, "000005.head.json")
            with open(already, "w") as f:
                f.write("ORIGINAL")
            anchor_head.main(base=site, anchors_dir=anchors)
            self.assertEqual(open(already).read(), "ORIGINAL")  # left untouched (don't re-stamp)

    def test_no_head_is_a_clean_noop(self):
        # The silent-failure guard: a missing/odd head must be an explicit no-op
        # (return 0, write nothing) -- pinned so a load_head regression is caught.
        with tempfile.TemporaryDirectory() as site, tempfile.TemporaryDirectory() as anchors:
            _site(site, {"draws": []})  # no head key
            self.assertEqual(anchor_head.main(base=site, anchors_dir=anchors), 0)
            self.assertEqual(os.listdir(anchors), [])


if __name__ == "__main__":
    unittest.main()
