#!/usr/bin/env python3
"""
get_stats_overview() powers the /stats chart: per-number frequencies + chi-square,
computed live from all draws. Uses the isolated `db` fixture.
"""


def test_stats_overview_shape_and_counts(db):
    from app.models import create_draw
    from app.lotto import get_stats_overview

    create_draw([(i, [1, 2, 3, 4, 5], [1, 2], "t", i * 144, i * 144 + 143) for i in range(4)])
    ov = get_stats_overview()

    assert ov["draws"] == 4
    assert len(ov["front"]["bars"]) == 35
    assert len(ov["back"]["bars"]) == 12
    # every draw used the same numbers, so the totals must add up
    assert sum(b["count"] for b in ov["front"]["bars"]) == 4 * 5
    assert sum(b["count"] for b in ov["back"]["bars"]) == 4 * 2
    assert ov["front"]["bars"][0]["count"] == 4  # number 1 in every front list
    assert {"chi2", "p_value", "conclusion"} <= ov["front"]["stats"].keys()
    # tallest bar normalizes to 100%
    assert max(b["pct"] for b in ov["front"]["bars"]) == 100


def test_stats_overview_none_when_empty(db):
    from app.lotto import get_stats_overview
    assert get_stats_overview() is None
