#!/usr/bin/env python3
"""
The cold-start seed CSV is no longer committed. create_bitcoin() must degrade
gracefully when it's absent (and no LOTTO_SEED_CSV_URL is set): start with an
empty block table instead of crashing. Uses the isolated `db` fixture.
"""


def test_create_bitcoin_skips_gracefully_when_seed_absent(db, monkeypatch):
    import app.models as models
    monkeypatch.setattr(models, "csv_file_name", "/nonexistent/seed.csv")
    monkeypatch.delenv("LOTTO_SEED_CSV_URL", raising=False)

    models.create_bitcoin()  # must not raise

    assert models.get_max_bitcoin_height() is None  # table left empty
