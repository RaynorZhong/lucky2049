#!/usr/bin/env python3
"""
Tests the disaster-recovery publisher (scripts/export_static.py): DB -> static
site. By definition it runs when everything else has already failed, yet it had
zero coverage -- a wrong ORDER BY or a skipped row would emit a wrong-but-
plausible commitment chain. Stdlib only (real sqlite, tmp dir, no network).
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import verify  # noqa: E402
import export_static  # noqa: E402


def _make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE draw (id INTEGER, front TEXT, back TEXT, start_height INTEGER, "
        "end_height INTEGER, algo_version TEXT, commitment TEXT, timestamp TEXT)")
    for r in rows:
        con.execute("INSERT INTO draw VALUES (?,?,?,?,?,?,?,?)",
                    (r["id"], json.dumps(r["front"]), json.dumps(r["back"]),
                     r["start_height"], r["end_height"], r["algo_version"],
                     r["commitment"], r["timestamp"]))
    con.commit()
    con.close()


def _export(db, out):
    with mock.patch.object(sys, "argv", ["export_static.py", "--out", out, "--db", db]):
        export_static.main()


class TestExportStatic(unittest.TestCase):
    def test_builds_chained_index_head_and_artifacts(self):
        c0, c1 = "a" * 64, "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            db, out = os.path.join(tmp, "db.sqlite"), os.path.join(tmp, "site")
            _make_db(db, [
                {"id": 0, "front": [11, 14, 19, 30, 35], "back": [2, 11], "start_height": 0,
                 "end_height": 143, "algo_version": "v1", "commitment": c0, "timestamp": "2025-01-01 00:00:00 UTC"},
                {"id": 1, "front": [8, 10, 11, 18, 23], "back": [6, 11], "start_height": 144,
                 "end_height": 287, "algo_version": "v1", "commitment": c1, "timestamp": "2025-01-02 00:00:00 UTC"},
            ])
            _export(db, out)

            idx = json.load(open(os.path.join(out, "index.json")))
            self.assertEqual(idx["count"], 2)
            # prev_commitment is re-threaded by row order: genesis, then each prior commitment.
            self.assertEqual(idx["draws"][0]["prev_commitment"], verify.GENESIS_PREV)
            self.assertEqual(idx["draws"][0]["commitment"], c0)
            self.assertEqual(idx["draws"][1]["prev_commitment"], c0)
            self.assertEqual(idx["draws"][1]["commitment"], c1)

            head = json.load(open(os.path.join(out, "head.json")))
            self.assertEqual(head, {"head": c1, "draw_id": 1, "count": 2, "algo_version": "v1"})

            latest = json.load(open(os.path.join(out, "latest.json")))
            self.assertEqual(latest["latest"]["id"], 1)
            feed = json.load(open(os.path.join(out, "feed.json")))
            self.assertEqual(feed["items"][0]["id"], "1")  # newest first
            st = json.load(open(os.path.join(out, "status.json")))
            self.assertEqual(st["sources"], [])           # offline rebuild probes nothing
            self.assertIn("note", st)

    def test_empty_db_writes_genesis_sentinel_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, out = os.path.join(tmp, "db.sqlite"), os.path.join(tmp, "site")
            _make_db(db, [])
            _export(db, out)
            head = json.load(open(os.path.join(out, "head.json")))
            self.assertEqual(head, {"head": verify.GENESIS_PREV, "draw_id": -1, "count": 0, "algo_version": "v1"})
            self.assertEqual(json.load(open(os.path.join(out, "index.json")))["count"], 0)


if __name__ == "__main__":
    unittest.main()
