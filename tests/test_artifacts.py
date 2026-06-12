#!/usr/bin/env python3
"""
Tests the downstream beacon artifacts writer (scripts/artifacts.py). feed.json is
a published consumer contract (docs/SCHEMA.md) -- ordering, the 30-item cap, the
RFC-3339 date, and the _lucky2049 structured extension the homepage now renders
from -- yet it had no direct assertions. Stdlib only, tmp dir, no network.
"""
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import artifacts  # noqa: E402


def _draw(i):
    return {"id": i, "front": [1, 2, 3, 4, 5], "back": [6, 7],
            "start_height": i * 144, "end_height": i * 144 + 143,
            "algo_version": "v1", "commitment": "c%d" % i, "prev_commitment": "p",
            "timestamp": "2026-01-01 00:00:%02d UTC" % (i % 60)}


def _feed(draws):
    with tempfile.TemporaryDirectory() as tmp:
        artifacts.write_feed(tmp, draws)
        return json.load(open(os.path.join(tmp, "feed.json")))


class TestWriteFeed(unittest.TestCase):
    def test_newest_first_and_capped_at_feed_items(self):
        feed = _feed([_draw(i) for i in range(artifacts.FEED_ITEMS + 5)])
        items = feed["items"]
        self.assertEqual(len(items), artifacts.FEED_ITEMS)        # truncated to the cap
        ids = [int(it["id"]) for it in items]
        self.assertEqual(ids, sorted(ids, reverse=True))          # strictly newest-first
        self.assertEqual(ids[0], artifacts.FEED_ITEMS + 4)        # the latest draw
        self.assertEqual(ids[-1], 5)                              # last FEED_ITEMS of 0..N

    def test_item_shape_and_rfc3339_date(self):
        it = _feed([_draw(0)])["items"][0]
        for k in ("id", "url", "title", "content_text", "date_published", "_lucky2049"):
            self.assertIn(k, it)
        self.assertEqual(it["id"], "0")                           # id is a string
        self.assertEqual(it["date_published"], "2026-01-01T00:00:00Z")  # RFC 3339
        self.assertTrue(it["url"].endswith("verify.html?draw=0"))

    def test_lucky2049_extension_carries_structured_numbers(self):
        ext = _feed([_draw(7)])["items"][0]["_lucky2049"]
        self.assertEqual(ext["front"], [1, 2, 3, 4, 5])
        self.assertEqual(ext["back"], [6, 7])
        self.assertEqual((ext["start_height"], ext["end_height"]), (7 * 144, 7 * 144 + 143))

    def test_missing_timestamp_omits_date_published(self):
        d = _draw(0)
        del d["timestamp"]
        it = _feed([d])["items"][0]
        self.assertNotIn("date_published", it)                    # optional field, cleanly absent
        self.assertIn("_lucky2049", it)                           # structured fields still present

    def test_empty_history_is_empty_feed(self):
        feed = _feed([])
        self.assertEqual(feed["items"], [])
        self.assertEqual(feed["version"], "https://jsonfeed.org/version/1.1")


if __name__ == "__main__":
    unittest.main()
