#!/usr/bin/env python3
"""
/api/draws is paginated (newest first) and caps limit so the response stays
bounded as draws accumulate. Uses the isolated client/db fixtures.
"""


def test_api_draws_paginates_newest_first(client):
    from app.models import create_draw
    create_draw([(i, [1, 2, 3, 4, 5], [1, 2], "t", i * 144, i * 144 + 143) for i in range(5)])

    body = client.get("/api/draws?limit=2").json()
    assert body["total"] == 5
    assert body["limit"] == 2 and body["offset"] == 0
    assert [d["id"] for d in body["draws"]] == [4, 3]  # newest first

    page2 = client.get("/api/draws?limit=2&offset=2").json()
    assert [d["id"] for d in page2["draws"]] == [2, 1]


def test_api_draws_caps_limit(client):
    assert client.get("/api/draws?limit=999999").json()["limit"] == 1000
