#!/usr/bin/env python3
"""
Tests for the idempotent lightweight schema migration in db/models.py.

Guards the fix for the "no such column: algo_version" class of bug: adding a
field to a model must auto-add the column to an existing database, exactly once,
and backfill the model default for legacy rows.

Skipped automatically when db.models' deps (pandas/sqlmodel/...) aren't
installed, so the stdlib-only CI job stays green; it runs in the full job.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    import db.models as models
    from sqlalchemy import create_engine
    HAVE_DEPS = True
except Exception as e:  # pragma: no cover - deps not installed in stdlib CI job
    HAVE_DEPS = False
    _IMPORT_ERROR = e


@unittest.skipUnless(HAVE_DEPS, "db.models not importable (deps missing)")
class TestLightweightMigration(unittest.TestCase):
    def setUp(self):
        self._orig_engine = models.engine
        fd, self.tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        # Build a LEGACY draw table: pre-algo_version shape, with one row.
        conn = sqlite3.connect(self.tmp)
        conn.execute(
            "CREATE TABLE draw (id INTEGER PRIMARY KEY, front VARCHAR, back VARCHAR, "
            "timestamp VARCHAR, start_height INTEGER, end_height INTEGER)"
        )
        conn.execute("INSERT INTO draw VALUES (0,'[1,2,3,4,5]','[1,2]','t',0,143)")
        conn.commit()
        conn.close()
        models.engine = create_engine(f"sqlite:///{self.tmp}")

    def tearDown(self):
        models.engine.dispose()
        models.engine = self._orig_engine
        os.remove(self.tmp)

    def _columns(self):
        conn = sqlite3.connect(self.tmp)
        try:
            return [r[1] for r in conn.execute("PRAGMA table_info(draw)")]
        finally:
            conn.close()

    def test_adds_missing_column_and_backfills_default(self):
        self.assertNotIn("algo_version", self._columns())
        models.create_db_and_tables()
        self.assertIn("algo_version", self._columns())
        conn = sqlite3.connect(self.tmp)
        try:
            val = conn.execute("SELECT algo_version FROM draw WHERE id=0").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(val, "v1")

    def test_idempotent_second_run(self):
        models.create_db_and_tables()
        cols_after_first = self._columns()
        models.create_db_and_tables()  # must not raise / duplicate
        self.assertEqual(self._columns(), cols_after_first)


if __name__ == "__main__":
    unittest.main()
