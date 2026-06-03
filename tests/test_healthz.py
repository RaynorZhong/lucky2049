#!/usr/bin/env python3
"""
TDD worked example: a /healthz endpoint.

Written test-first — these assertions existed (and failed with 404) before the
route did. Demonstrates the `client` fixture (isolated DB, no lifespan), so the
test sees an empty database: zero draws, the genesis commitment head.
"""


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["algo_version"] == "v1"


def test_healthz_reports_empty_db_state(client):
    # The `client` fixture's DB is freshly created and empty.
    body = client.get("/healthz").json()
    assert body["draws"] == 0
    assert body["latest_draw_id"] is None
    assert body["commitment_head"] == "0" * 64  # GENESIS_PREV
