#!/usr/bin/env python3
"""
Tests for verify.py's --site comparison plumbing (verify._fetch_published).

The public deployment is the static GitHub Pages snapshot (index.json, no API),
so --site must fall back from the live manifest API to index.json and still
surface the result + commitment chain. Stdlib only -- runs in the fast CI job.
"""
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import verify  # noqa: E402

# Golden draw 0 (from SPEC.md, same vectors as test_commitment.py).
DRAW0_SEED = "cbc014f38f94d72431f9e1d2f978ff3db74a0be3ffa0e8fcfc1af92818ea324c"
DRAW0_COMMITMENT = "def4cd38f63acc6f39a9c4dbe0df021c00e219907cbfeb58752be198092b0739"
DRAW0_FRONT = [11, 14, 19, 30, 35]
DRAW0_BACK = [2, 11]

SITE = "https://lucky2049.com"

# A static snapshot record shaped exactly like scripts/export_static.py writes.
STATIC_INDEX = {
    "count": 1,
    "head": {"head": DRAW0_COMMITMENT, "draw_id": 0, "count": 1, "algo_version": "v1"},
    "algo_version": "v1",
    "draws": [{
        "id": 0,
        "start_height": 0, "end_height": 143,
        "front": DRAW0_FRONT, "back": DRAW0_BACK,
        "algo_version": "v1",
        "commitment": DRAW0_COMMITMENT,
        "prev_commitment": verify.GENESIS_PREV,
        "timestamp": "2025-01-01T00:00:00",
    }],
}


def _static_only_http_get(url, timeout=30):
    """Simulate a static GitHub Pages host: the API 404s, index.json serves."""
    if url.endswith("/index.json"):
        return json.dumps(STATIC_INDEX)
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def _live_api_http_get(url, timeout=30):
    """Simulate a live server that exposes the manifest API."""
    if url.endswith("/api/draw/0/manifest"):
        return json.dumps({
            "result": {"front": DRAW0_FRONT, "back": DRAW0_BACK},
            "block_hashes": ["a" * 64] * verify.NUM_BLOCKCHAIN,
            "commitment": DRAW0_COMMITMENT,
            "algo_version": "v1",
        })
    raise AssertionError(f"unexpected url {url}")


class TestFetchPublishedStaticFallback(unittest.TestCase):
    def test_falls_back_to_index_json(self):
        with mock.patch.object(verify, "_http_get", _static_only_http_get):
            pub = verify._fetch_published(SITE, 0)
        self.assertEqual(pub["kind"], "static")
        self.assertEqual(pub["front"], DRAW0_FRONT)
        self.assertEqual(pub["back"], DRAW0_BACK)
        self.assertEqual(pub["commitment"], DRAW0_COMMITMENT)
        self.assertEqual(pub["prev_commitment"], verify.GENESIS_PREV)
        self.assertEqual(pub["block_hashes"], [])  # snapshot omits hashes by design

    def test_static_record_reproduces_golden_commitment(self):
        # What _fetch_published surfaces must be exactly what recomputes the
        # published commitment -- i.e. the --site CHAIN MATCH check would PASS.
        with mock.patch.object(verify, "_http_get", _static_only_http_get):
            pub = verify._fetch_published(SITE, 0)
        recomputed = verify.commitment_for(
            pub["prev_commitment"], 0, pub["algo_version"], DRAW0_SEED,
            pub["front"], pub["back"], 0, 143,
        )
        self.assertEqual(recomputed, pub["commitment"])

    def test_missing_draw_raises(self):
        with mock.patch.object(verify, "_http_get", _static_only_http_get):
            with self.assertRaises(Exception):
                verify._fetch_published(SITE, 999)


class TestFetchPublishedLiveApi(unittest.TestCase):
    def test_prefers_manifest_api(self):
        with mock.patch.object(verify, "_http_get", _live_api_http_get):
            pub = verify._fetch_published(SITE, 0)
        self.assertEqual(pub["kind"], "api")
        self.assertEqual(pub["front"], DRAW0_FRONT)
        self.assertEqual(len(pub["block_hashes"]), verify.NUM_BLOCKCHAIN)
        self.assertIsNone(pub["prev_commitment"])  # live API: resolved separately


if __name__ == "__main__":
    unittest.main()
