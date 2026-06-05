#!/usr/bin/env python3
"""
Downstream-consumable beacon artifacts, written next to index.json so other
projects can build on lucky2049 *draws*. (This project publishes only the draw --
no prize pool, tickets, or payouts; those belong to separate downstream projects.)

  latest.json  - the newest draw + the history head; poll this for "what's new"
  feed.json    - JSON Feed 1.1 of the most recent draws; subscribe to this

Schemas + the stability promise live in docs/SCHEMA.md. Field names are stable;
changes are additive only, and the algorithm version is declared per draw.
"""
import json
import os

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


def _write(out_dir, name, obj):
    with open(os.path.join(out_dir, name), "w") as f:
        json.dump(obj, f, separators=(",", ":"))


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


def write_beacon(out_dir, draws, head):
    """Write all downstream-facing artifacts (latest.json + feed.json)."""
    write_latest(out_dir, draws, head)
    write_feed(out_dir, draws)
