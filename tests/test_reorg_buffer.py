#!/usr/bin/env python3
"""
Tests for the reorg-safety confirmation buffer (bitcoin.py).

Ingestion must lag the chain tip by CONFIRMATIONS so our append-only store
never captures a block that a shallow reorg could later rewrite. This does not
affect the v1 result for any height — only which blocks are considered final.

Skips when bitcoin.py's deps aren't installed (stdlib-only CI job).
"""
import hashlib
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    import app.bitcoin as bitcoin
    HAVE_DEPS = True
except Exception as e:  # pragma: no cover - deps not installed in stdlib CI job
    HAVE_DEPS = False
    _IMPORT_ERROR = e


def _fake_blocks(start, count):
    return [
        {"height": h, "hash": hashlib.sha256(f"h{h}".encode()).hexdigest(), "timestamp": "2025-01-01T00:00:00Z"}
        for h in range(start, start + count)
    ]


@unittest.skipUnless(HAVE_DEPS, "bitcoin.py not importable (deps missing)")
class TestConfirmedTip(unittest.TestCase):
    def test_confirmed_tip_lags_by_confirmations(self):
        k = bitcoin.CONFIRMATIONS
        self.assertEqual(bitcoin.confirmed_tip(1000), 1000 - k)
        self.assertEqual(bitcoin.confirmed_tip(k), 0)

    def test_confirmed_tip_negative_when_chain_too_short(self):
        self.assertLess(bitcoin.confirmed_tip(0), 1)


@unittest.skipUnless(HAVE_DEPS, "bitcoin.py not importable (deps missing)")
class TestUpdateBitcoinsLag(unittest.TestCase):
    def setUp(self):
        self._orig = {n: getattr(bitcoin, n) for n in
                      ("get_max_bitcoin_height", "fetch_height", "fetch_bitcoins", "add_bitcoin")}
        self.ingested = []
        bitcoin.add_bitcoin = lambda rows: (self.ingested.extend(h for h, _, _ in rows) or True)
        bitcoin.fetch_bitcoins = _fake_blocks

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(bitcoin, n, v)

    def test_stops_at_confirmed_tip(self):
        tip = 1000
        bitcoin.get_max_bitcoin_height = lambda: 900
        bitcoin.fetch_height = lambda: tip
        bitcoin.update_bitcoins()
        self.assertTrue(self.ingested)
        # Never ingest beyond tip - CONFIRMATIONS.
        self.assertEqual(max(self.ingested), tip - bitcoin.CONFIRMATIONS)
        self.assertLessEqual(max(self.ingested), tip - bitcoin.CONFIRMATIONS)

    def test_no_ingest_when_nothing_confirmed(self):
        # Only unconfirmed blocks exist beyond our store.
        bitcoin.get_max_bitcoin_height = lambda: 1000
        bitcoin.fetch_height = lambda: 1000 + bitcoin.CONFIRMATIONS - 1
        bitcoin.update_bitcoins()
        self.assertEqual(self.ingested, [])


if __name__ == "__main__":
    unittest.main()
