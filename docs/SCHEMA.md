# Data Schema & Consumer Guide

> 🌏 **English** · [中文](zh/SCHEMA.md)

lucky2049 is a **verifiable public randomness beacon**: it publishes only the *draw* (the numbers
plus a tamper-evidence chain), derived deterministically from 144 Bitcoin block hashes (see
[`SPEC.md`](../SPEC.md)). Prize pools, ticketing, and payouts are intentionally **out of scope** —
they belong to separate, downstream projects. This page is the stable contract those projects
build on.

Everything is plain static JSON under the site root (`https://lucky2049.com/…`), served by GitHub
Pages — no API, no auth, no rate limits beyond the CDN.

## Published files

| File | Use |
|------|-----|
| [`latest.json`](https://lucky2049.com/latest.json) | Newest draw + the history head. **Poll this** for "what's new". |
| [`feed.json`](https://lucky2049.com/feed.json) | [JSON Feed 1.1](https://jsonfeed.org) of the ~30 most recent draws. **Subscribe to this.** |
| [`status.json`](https://lucky2049.com/status.json) | Health of the last refresh: per-source probe results (Core node / explorers). |
| [`index.json`](https://lucky2049.com/index.json) | Full history: every draw + the head (~2 MB, ~0.5 MB gzipped, growing ~daily). |
| [`head.json`](https://lucky2049.com/head.json) | The commitment head alone (a 32-byte hash committing to all history). |
| `anchors/<id>.head.json.ots` | OpenTimestamps proofs anchoring a head onto the Bitcoin chain. |

## Schemas

### Draw record
The object in `index.json`'s `draws[]` and in `latest.json`'s `latest`:

```jsonc
{
  "id": 6611,                              // draw number; heights = [id*144, id*144+143]
  "algo_version": "v1",                    // algorithm version this draw used (see SPEC.md)
  "front": [4, 6, 14, 19, 27],             // 5 distinct, ascending, 1–35
  "back": [3, 11],                         // 2 distinct, ascending, 1–12
  "start_height": 951984,                  // first Bitcoin block height
  "end_height": 952127,                    // last (= start_height + 143)
  "commitment": "<64-hex>",                // SHA-256 hash-chain commitment for this draw
  "prev_commitment": "<64-hex>",           // previous draw's commitment (genesis sentinel for draw 0)
  "timestamp": "2026-06-02 16:05:05 UTC"   // last block's time (display only; NOT committed)
}
```
In `latest.json` (and other curated views) the record also carries `verify_url`, a convenience link
to the in-browser verifier.

### `index.json`
```jsonc
{ "count": 6612, "algo_version": "v1", "head": <head>, "draws": [ <draw record>, … ] }  // oldest first
```

### `head` object  (in `head.json`, `index.json.head`, `latest.json.head`)
```jsonc
{ "head": "<64-hex>", "draw_id": 6611, "count": 6612, "algo_version": "v1" }
```

### `latest.json`
```jsonc
{ "schema": "lucky2049/latest/v1", "head": <head>, "latest": <draw record + verify_url> }
```

### `feed.json`
Standard [JSON Feed 1.1](https://jsonfeed.org/version/1.1): `version`, `title`, `home_page_url`,
`feed_url`, `items[]`. Each item: `id` (draw id as a string), `url` (verifier link), `title`,
`content_text`, `date_published` (RFC 3339). The ~30 most recent draws, newest first.

Each item also carries a `_lucky2049` extension object (JSON Feed reserves `_`-prefixed members for
publisher extensions; plain feed readers ignore it) with the structured numbers, so a consumer need
not re-parse the display strings: `{ "front": [int×5], "back": [int×2], "start_height", "end_height" }`.

### `status.json`
Written by every publisher run (hourly): did each hash source answer, and what did it say?

```jsonc
{
  "schema": "lucky2049/status/v1",
  "checked_at": "2026-06-11 07:20:12 UTC",
  "checked_at_unix": 1781075212,
  "tip_source": "core",                    // first source that answered (preference order); not necessarily the quorum tip that gated maturity
  "sources": [                             // preference order; "core" only when configured
    { "name": "core", "ok": true, "tip": 953202, "ms": 312 },
    { "name": "mempool", "ok": true, "tip": 953202, "ms": 145 },
    { "name": "blockstream", "ok": false, "error": "HTTP Error 503: ...", "ms": 1042 }
  ],
  "head": { "head": "<64-hex>", "draw_id": 6618, "count": 6619, "algo_version": "v1" },
  "added": 0,                              // draws published by this run
  "held": 6619,                            // present only when a draw was held (source disagreement)
  "note": "…"                              // present only on an anomaly (see below) or an offline rebuild
}
```
The homepage renders this as the "Sources (last refresh)" strip. The cron is hourly, but
GitHub Actions schedules drift, so `checked_at` older than ~4 h (not ~1 h) is what indicates
the cron itself isn't running.

`note` is set only on something worth attention: in a **live run** either a reorg self-audit
result mismatch (a recently-committed draw no longer matches the chain) or a quorum tip that
regressed below the last committed window — the workflow alarms on either. An **offline rebuild**
(`export_static.py`, disaster recovery) also sets `note`, to say nothing was probed (`sources: []`,
`tip_source: null`); the next hourly run replaces it with real probes. And when sources were probed
but **none were ok**, the publisher still ships the (all-red) file and the run fails loudly as the alarm.

## Consume

- **"What's the newest draw?"** → GET `latest.json` (tiny) on whatever cadence you like; a new draw
  matures ~daily.
- **"Subscribe."** → point a JSON Feed reader at `feed.json` (autodiscoverable via the homepage's
  `<link rel="alternate">`).
- **"Give me everything."** → GET `index.json` once and cache it; it carries every draw.

## Verify — don't trust, recompute

Every draw is independently reproducible from the chain; don't take the published numbers on faith:

- CLI: `python verify.py <id> --site https://lucky2049.com`
- Browser: open `verify.html?draw=<id>`
- Tamper-evidence: each draw's `commitment` chains into `head.json`'s head; the proofs under
  `anchors/` timestamp that head onto Bitcoin. See [`SPEC.md`](../SPEC.md) §5 and the README.

## Stability promise

- **Additive only.** Existing field names and meanings don't change; new fields may be added.
  Top-level `schema` tags (e.g. `lucky2049/latest/v1`) bump only on a breaking change.
- **Per-draw versioning.** Each draw declares `algo_version`; `v1` is frozen, and any future
  algorithm change applies only to *new* draws (historical draws stay verifiable under the version
  they declared).
- **Heights are derivable**, never operator-chosen: draw `N` always uses `[N*144, N*144+143]`.

## Scope — the red line

This is a **beacon**, not a lottery operator. It runs **no prize pool, no ticket sales, and no
payouts** — by design, to stay clear of gambling regulation. If you build a prize on top, you own
that risk; keep any single prize below the economic-security ceiling in [`SPEC.md`](../SPEC.md) §8
(`W < B/p`).
