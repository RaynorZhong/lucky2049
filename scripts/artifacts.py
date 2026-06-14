#!/usr/bin/env python3
"""
Downstream-consumable beacon artifacts, written next to index.json so other
projects can build on lucky2049 *draws*. (This project publishes only the draw --
no prize pool, tickets, or payouts; those belong to separate downstream projects.)

  latest.json  - the newest draw + the history head; poll this for "what's new"
  feed.json    - JSON Feed 1.1 of the most recent draws; subscribe to this
  status.json  - per-source health of the last refresh (probe results for the
                 Core node / mempool / blockstream); the operator's RPC monitor

Schemas + the stability promise live in docs/SCHEMA.md. Field names are stable;
changes are additive only, and the algorithm version is declared per draw.
"""
import datetime
import json
import os
import tempfile

SITE_URL = "https://lucky2049.com"
FEED_ITEMS = 30  # most-recent draws carried in feed.json


def public_draw(d):
    """A published index record for one draw, plus a convenience verify_url."""
    out = dict(d)
    out["verify_url"] = f"{SITE_URL}/verify.html?draw={d['id']}"
    return out


def _rfc3339(ts):
    # "YYYY-MM-DD HH:MM:SS UTC" -> "YYYY-MM-DDTHH:MM:SSZ" (JSON Feed date_published)
    return ts.replace(" UTC", "Z").replace(" ", "T", 1) if ts else None


def atomic_write_json(path, obj, **dumpargs):
    """Write JSON atomically (temp file in the same dir + os.replace), so a crash
    mid-write can never leave a truncated file that the next run fails to load."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix="-" + os.path.basename(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, **dumpargs)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write(out_dir, name, obj):
    atomic_write_json(os.path.join(out_dir, name), obj, separators=(",", ":"))


def write_latest(out_dir, draws, head):
    _write(out_dir, "latest.json", {
        "schema": "lucky2049/latest/v1",
        "head": head,
        "latest": public_draw(draws[-1]) if draws else None,
    })


def write_feed(out_dir, draws):
    items = []
    for d in reversed(draws[-FEED_ITEMS:]):  # newest first
        front = " ".join("%02d" % n for n in d["front"])
        back = " ".join("%02d" % n for n in d["back"])
        item = {
            "id": str(d["id"]),
            "url": f"{SITE_URL}/verify.html?draw={d['id']}",
            "title": f"Draw {d['id']}: {front} + {back}",
            "content_text": f"Front {front} | Back {back}  (heights {d['start_height']}-{d['end_height']})",
            # JSON Feed extension (members starting with "_" are ignored by plain
            # feed readers): structured fields so a consumer -- e.g. our own
            # homepage table -- can render without re-parsing the display strings.
            "_lucky2049": {
                "front": d["front"],
                "back": d["back"],
                "start_height": d["start_height"],
                "end_height": d["end_height"],
            },
        }
        iso = _rfc3339(d.get("timestamp"))
        if iso:
            item["date_published"] = iso
        items.append(item)
    _write(out_dir, "feed.json", {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "lucky2049 draws",
        "home_page_url": SITE_URL,
        "feed_url": f"{SITE_URL}/feed.json",
        "description": "Verifiable Super Lotto draws derived from 144 Bitcoin block hashes.",
        "items": items,
    })


def write_status(out_dir, sources, checked_unix, head, added=0, held=None, note=None):
    """status.json -- health of the last refresh, one entry per hash source.

    `sources` comes from the publisher's per-run probe: [{name, ok, tip, ms} |
    {name, ok: false, error, ms}], in preference order. Lets the operator see at
    a glance whether their self-hosted Core RPC (and the explorers) answered."""
    ts = datetime.datetime.fromtimestamp(int(checked_unix), tz=datetime.timezone.utc)
    obj = {
        "schema": "lucky2049/status/v1",
        "checked_at": ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "checked_at_unix": int(checked_unix),
        "tip_source": next((s["name"] for s in sources if s.get("ok")), None),
        "sources": sources,
        "head": head,
        "added": added,
    }
    if held is not None:
        obj["held"] = held
    if note:
        obj["note"] = note
    _write(out_dir, "status.json", obj)


def status_problems(status, min_agreement=2):
    """Reasons the last refresh should ALARM the operator (empty list = healthy).
    The single source of truth for the refresh-pages.yml alarm step, so the decision
    is unit-testable and can't silently drift from write_status's field names."""
    srcs = status.get("sources") or []   # empty for an offline rebuild stub -> no source alarm
    healthy = sum(1 for s in srcs if s.get("ok"))
    problems = []
    if srcs and healthy == 0:
        problems.append("ALL SOURCES DOWN")
    elif srcs and healthy < min_agreement:
        problems.append(f"only {healthy} healthy source(s) < MIN_SOURCE_AGREEMENT={min_agreement} -- drawing stalled")
    if status.get("held") is not None:
        problems.append(f"draw {status['held']} HELD -- sources disagree on a confirmed window")
    if status.get("note"):
        problems.append(status["note"])
    return problems


def write_beacon(out_dir, draws, head):
    """Write all downstream-facing artifacts (latest.json + feed.json)."""
    write_latest(out_dir, draws, head)
    write_feed(out_dir, draws)
