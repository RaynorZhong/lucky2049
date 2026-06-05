# lucky2049 — Verifiable Bitcoin-Hash Draw System

> 🌏 **English** · [中文](docs/zh/README.md)

## Overview

**lucky2049** is a **draw-only**, transparent, independently reproducible number-drawing
system. It uses **144 consecutive Bitcoin mainnet block hashes** as a public, unpredictable
entropy source, and a deterministic algorithm to produce one **Super Lotto** draw:

- front: 5 distinct numbers, range 1–35
- back: 2 distinct numbers, range 1–12

The algorithm is deterministic: anyone with the same 144 block hashes can **reproduce the
exact same draw, bit for bit**. The source of truth is the Bitcoin blockchain itself
(network consensus — objective and tamper-proof), not this system's database or any single API.

It ships as a **pure static, server-less** site (GitHub Pages → lucky2049.com): a GitHub
Actions cron draws + publishes, and verification happens entirely in the browser / on the
command line — no backend, and no database on the critical path.

> **Scope:** this project **only does the draw**. Prize pools, ticket sales, and payouts are
> out of scope and left to other projects — deliberately, to avoid potential legal risk. It is
> for research and entertainment only and is not a gambling service.

**Algorithm spec:** see [`SPEC.md`](SPEC.md) (frozen version `v1`).
**Independent verification:** run [`verify.py`](verify.py) on the command line, or open
`verify.html` to self-verify in the browser.
**Repository:** https://github.com/RaynorZhong/lucky2049 · **Demo:** https://lucky2049.com

## Fairness properties

| Property | Status | Notes |
|------|------|------|
| Reproducible | ✅ | Open deterministic algorithm + public on-chain data; anyone can recompute |
| Zero operator discretion | ✅ | Draw N always uses heights `[N*144, N*144+143]`, anchored at genesis — no manual selection |
| Algorithm frozen | ✅ | `ALGO_VERSION="v1"`, declared per draw; rule changes require a new version and apply only to future draws |
| Tamper-evident (immutable history) | ✅ | Each draw is committed into a hash chain; the whole history collapses to one "head". Anchoring the head externally blocks after-the-fact rewrites (see "Tamper-evidence") |
| Reorg-resistant | ✅ | Blocks must lag `DRAW_CONFIRMATIONS` (default 6) confirmations before they count; a shallow reorg can't change a published result |
| Miner manipulation | ⚠️ economic | Aggregating 144 blocks pushes the attack cost very high; this is economic, not cryptographic, security — residual risk grows with downstream prize size (see SPEC.md §7) |

## Algorithm (v1 summary)

1. Take draw N's 144 blocks (ascending height) and concatenate their 64-char lowercase-hex
   hashes with no separator into `combined`.
2. `seed = SHA256(utf8(combined))` (32-byte digest).
3. For `counter = 0..6`: `int_k = HMAC_SHA256(seed, ascii(str(counter)))`, read as a 256-bit
   big-endian integer.
4. Front: from pool `[1..35]`, repeatedly `pop` at `idx = int_i mod len(pool)`; take 5, sort ascending.
5. Back: from pool `[1..12]`, same method; take 2, sort ascending.

Full spec and test vectors in [`SPEC.md`](SPEC.md).

## Architecture

The whole "draw + publish + verify" loop runs on GitHub, with **no server and no database**:

- **Drawer (cron)** — [`.github/workflows/refresh-pages.yml`](.github/workflows/refresh-pages.yml)
  periodically runs [`scripts/extend_pages.py`](scripts/extend_pages.py): for each **fully
  confirmed** new 144-block window it fetches the hashes from **≥2 independent sources and
  requires them to agree**, recomputes with `verify.py`, chains the commitment, appends to
  `index.json`, and pushes back to `gh-pages` (a draw whose sources disagree is held, not
  published — so a single bad/forked explorer can't corrupt history). Pure stdlib.
- **Site** — `web/` (`index.html` [+ next-draw ETA] / `verify.html` / `stats.html` /
  `trend.html` [trend chart]) + `static/` (`verify.js` / `stats.js` / `trend.js` / `style.css`).
  The browser reads `index.json` and recomputes with its own SHA-256/HMAC — trusting no server,
  touching no database.
- **Verifier** — `verify.py`: a standalone stdlib script that recomputes a draw + checks the
  commitment chain on the command line.

> A window takes ~144 blocks (≈ 24h) to mature; the cron runs **hourly**, so a matured window
> publishes within the hour (most runs are no-ops, skipped by the diff-guard). Still server-less.

### Local preview

```shell
python scripts/export_static.py --out /tmp/site   # build index.json + pages from the local DB cache
python -m http.server -d /tmp/site 8000           # open http://localhost:8000
```

## Data

The site **is** the data source — no dynamic API:

- `index.json` — the full, slim snapshot: `{count, head, algo_version, draws:[…]}`, each draw
  carrying id / height range / front + back / algorithm version / commitment / previous
  commitment / timestamp (**no 144 hashes**, ~2MB; hashes are fetched from the chain on demand,
  since the chain is the source of truth).
- `head.json` — the history **head**: a 32-byte hash committing to the entire draw history
  (anchor it externally to pin history; see "Tamper-evidence").
- `latest.json` — the newest draw + the head; poll this. `feed.json` — a
  [JSON Feed](https://jsonfeed.org) of recent draws; subscribe to this.

**Building on lucky2049?** The draws are a public beacon you can consume — see the data contract in
[`docs/SCHEMA.md`](docs/SCHEMA.md). (This project publishes only the draw; prize pools / tickets /
payouts are out of scope.)

## Verify

`verify.py` is self-contained and stdlib-only. Given a draw id, it pulls the 144 hashes from an
independent source, recomputes per SPEC v1, and compares the result + commitment chain against
the published snapshot:

```shell
# recompute from a public explorer and compare against the published site (RESULT MATCH + CHAIN MATCH)
python verify.py 6315 --source mempool --site https://lucky2049.com

# use your own full node as the source of truth
export BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"
python verify.py 6315 --source core

# offline, against a local database cache
python verify.py 6315 --source db --db data/database.db
```

Prefer not to use the command line? Just open the site's `verify.html` and run the same checks
in the browser with one click.

## Tamper-evidence

Every draw is chained into a SHA-256 hash-chain commitment:

```
commitment = SHA256( prev_commitment | draw_id | algo_version | seed | front | back | height_range )
```

So the entire history compresses to a single 32-byte **head** (`head.json`). Changing any draw
changes the head. **The head is anchored weekly to the Bitcoin blockchain via
[OpenTimestamps](https://opentimestamps.org)**
([`.github/workflows/anchor-head.yml`](.github/workflows/anchor-head.yml); proofs are published
under [`anchors/`](anchors/) and also served at `https://lucky2049.com/anchors/`), so an old head
is fixed by a third-party timestamp and the operator can't quietly rewrite history after the
fact. The key point: both `verify.py` and `verify.html` **independently recompute** this chain —
the commitment isn't the operator's word; anyone can `ots verify anchors/<id>.head.json.ots` to
check an anchor.

## Structure

```
verify.py       standalone engine (stdlib, single copyable file): the `generate` algorithm,
                commitment chain `commitment_for`, block-hash fetch (Core RPC / mempool /
                blockstream / sqlite), and the `--site` CLI verifier
scripts/
  extend_pages.py  cron drawer/publisher: extend index.json from the chain (stdlib, reuses verify.py, no DB)
  export_static.py rebuild the whole index.json + site from a local SQLite cache (stdlib sqlite3) — initial build / disaster recovery
  publish-pages.sh manual publish (export_static + push gh-pages); use the cron normally, pick one
web/            index.html (home + next-draw ETA) / verify.html / stats.html / trend.html (trend chart) + CNAME
static/         verify.js (verifier), stats.js (frequency + chi-square), trend.js (trend chart), style.css, favicon.svg — own algorithm, no external scripts
.github/workflows/  refresh-pages.yml (cron publish), tests.yml (algorithm / commitment / JS locks)
SPEC.md         frozen algorithm spec v1        docs/DEPLOY.md deployment   docs/TDD.md TDD workflow
data/           database.db — optional local cache, gitignored, not shipped with the repo; read only by export_static on rebuild
```

> The old FastAPI server + database + Docker/Render have been removed from `main` and archived
> in git tag `v1-server`; recover them from there if you ever need a live API. `lucky.py`
> (gitignored) is a standalone economic simulator, unrelated to the draw.

## Tests

```shell
make install-dev   # install pytest tooling only (runtime is pure stdlib)
make test          # run once
make watch         # re-run on save (TDD red-green loop)
make cov           # with a coverage report
python -m unittest discover -s tests   # works without pytest too (same suite)
```

Tests are **stdlib + Node** (no DB, no fixtures): golden-vector locks for the algorithm /
commitment (`test_spec_v1`, `test_commitment`), the standalone verifier (`test_verify_site`),
and the browser JS cross-checked under Node (`test_verify_js`, `test_stats_js`). TDD workflow in
[`docs/TDD.md`](docs/TDD.md). CI (GitHub Actions) runs these locks on every push / PR.

## License

MIT License — see [LICENSE](LICENSE). This project is for research and entertainment only, is not
a gambling service, and you must comply with your local laws.
