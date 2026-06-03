#!/usr/bin/env python3
"""
The log table is bounded by prune_logs() (scheduled daily). It must keep only
the most recent `keep` rows and delete the rest. Uses the isolated `db` fixture.
"""


def test_prune_logs_keeps_only_most_recent(db):
    from app.models import LogEntry, prune_logs, Session, engine, select, func

    with Session(engine) as s:
        for i in range(5):
            s.add(LogEntry(timestamp="t", level="WARNING", logger_name="x", message=f"m{i}"))
        s.commit()

    deleted = prune_logs(keep=2)
    assert deleted == 3

    with Session(engine) as s:
        remaining = s.exec(select(func.count()).select_from(LogEntry)).one()
        kept = [r.message for r in s.exec(select(LogEntry).order_by(LogEntry.id))]
    assert remaining == 2
    assert kept == ["m3", "m4"]  # the two newest


def test_prune_logs_noop_when_under_limit(db):
    from app.models import LogEntry, prune_logs, Session, engine

    with Session(engine) as s:
        s.add(LogEntry(timestamp="t", level="WARNING", logger_name="x", message="only"))
        s.commit()

    assert prune_logs(keep=2000) == 0
