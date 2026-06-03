"""
Pytest fixtures for fast, isolated tests.

The most important lines run at import time, BEFORE any test module imports
db.models / lotto / bitcoin / main: they point the database at a throwaway file
and disable the DB log handler. So nothing in the suite can touch the real
170MB database, and `from db.models import *` everywhere shares this one
throwaway engine.

Fixtures:
  db      - pristine, empty tables for one test (drop+create on the shared engine)
  client  - FastAPI TestClient with NO lifespan (no init_db / scheduler / CSV seed),
            backed by the isolated db fixture; for endpoint tests.
"""
import os
import sys
import tempfile

# Must be set before db.models is imported anywhere.
os.environ.setdefault("LOTTO_DISABLE_DB_LOG", "1")
_TMP_DIR = tempfile.mkdtemp(prefix="lucky2049-tests-")
os.environ.setdefault("LOTTO_DB_URL", "sqlite:///" + os.path.join(_TMP_DIR, "default.db"))

# Make the repo root importable (verify, lotto, bitcoin, main, db).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def db():
    """Give the test a pristine, empty database on the shared throwaway engine.

    Every module bound `engine` to the same object via `from db.models import *`,
    so dropping+recreating tables on it isolates each test without any patching.
    """
    import db.models as models
    models.SQLModel.metadata.drop_all(models.engine)
    models.SQLModel.metadata.create_all(models.engine)
    yield models.engine
    models.SQLModel.metadata.drop_all(models.engine)


@pytest.fixture
def client(db):
    """A TestClient with an isolated DB and no startup side effects.

    Instantiated without `with`, so Starlette does NOT run the lifespan
    (init_db / scheduler / CSV seeding); the `db` fixture has already provided
    migrated, empty tables.
    """
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)
