# CLAUDE.md

Guidance for Claude Code working in this repo. Keep it short and current.

## What this is

**lucky2049** is a **draw-engine only** lottery system: it turns 144 consecutive
Bitcoin mainnet block hashes into a Super Lotto result (front 5/35, back 2/12)
via a deterministic algorithm anyone can reproduce. **Scope is deliberately just
the draw** — no prize pool, ticketing, or payout (those are separate projects,
to avoid legal risk). Don't add gambling/business features here.

It ships as a **static, server-less site** (GitHub Pages → lucky2049.com): a
GitHub Actions cron draws + publishes, and the browser verifies. No backend, no
database in the critical path. (The old FastAPI server + DB live in the
`v1-server` git tag if a live API is ever needed again.)

## ⚠️ The algorithm is FROZEN (read before touching draw logic)

`SPEC.md` is the normative, frozen spec (`ALGO_VERSION = "v1"`). The number
generation in `verify.py` (`generate`) and the in-browser `static/verify.js`
must stay **bit-for-bit identical** and match `SPEC.md`.

- Do NOT change the generation logic, game params, seeding (SHA256→HMAC), or the
  commitment formula to "improve" them. Any real change requires a NEW version
  (`v2`, …) that applies to FUTURE draws only; historical draws stay verifiable.
- Golden-vector tests (`tests/test_spec_v1.py`, `tests/test_commitment.py`) and
  the JS parity tests (`tests/test_verify_js.py`, `tests/test_stats_js.py`) are
  **guardrails, not TODOs**. If a change turns them red, the change is wrong.
- `verify.py` is the single Python implementation; `static/verify.js` is the JS
  copy. They cross-check each other (Python ↔ JS) — keep them in lockstep.

## Run

It's a static site — nothing to serve in production. To preview locally:

```shell
python scripts/export_static.py --out /tmp/site   # build index.json + pages from the local DB cache
python -m http.server -d /tmp/site 8000           # open http://localhost:8000
```
The published site self-updates: `.github/workflows/refresh-pages.yml` (daily
cron) runs `scripts/extend_pages.py` to draw any newly-confirmed 144-block window
from the chain and republish `gh-pages`. No server, no DB — stdlib + `verify.py`.

## Test / TDD

```shell
make install-dev   # pytest tooling only (runtime is stdlib-only)
make test          # run once     make watch  # re-run on save (TDD loop)     make cov
python -m unittest discover -s tests   # stdlib-only fallback (same suite)
```
The suite is **stdlib + Node only** (no DB, no fixtures): golden-vector locks for
the algorithm/commitment (`test_spec_v1`, `test_commitment`), the standalone
verifier (`test_verify_site`), and the in-browser JS run under Node
(`test_verify_js`, `test_stats_js`). Write tests first — see `docs/TDD.md`.

## Key files

`verify.py` is the standalone engine; the site lives in `web/` + `static/`.

- `verify.py` — the draw algorithm (`generate`), commitment chain (`commitment_for`),
  block-hash fetch (Core RPC / mempool.space / blockstream / sqlite), and the
  `--site` CLI verifier. Stdlib only.
- `scripts/extend_pages.py` — the cron drawer/publisher: extends `index.json` from
  the chain (stdlib, reuses `verify.py`, no DB). Run by `refresh-pages.yml`.
- `scripts/export_static.py` — (re)build the full `index.json` + site from a local
  SQLite cache via stdlib `sqlite3` (initial build / disaster recovery).
- `web/` — `index.html` (+ next-draw ETA) / `verify.html` / `stats.html` /
  `trend.html` (Sina-style 走势图) + `CNAME`. `static/` — `verify.js`, `stats.js`,
  `trend.js`, `style.css`, `favicon.svg`. Light/minimal theme; front balls =
  blue, back = orange (don't surface "Litecoin"/"Bitcoin" as ball labels in the UI).
- `SPEC.md` (frozen spec) · `docs/DEPLOY.md` · `docs/TDD.md`.

## Gotchas

- **Single publish source**: the cron owns `gh-pages`/`index.json`. Don't also run
  `scripts/publish-pages.sh` locally — it force-pushes a DB export that can clash.
- **Custom domain**: `web/CNAME` (lucky2049.com) must ride along every publish, or
  a `gh-pages` force-push drops the domain → 404. Both publishers copy it.
- `data/database.db` is an **optional local cache** (gitignored, ~170MB, NOT in the
  repo), only read by `export_static.py` for a local rebuild. The cron never uses it.
- Anchor `head.json` externally (OpenTimestamps / git tag) to make history truly
  tamper-evident — code provides the chain, anchoring is the rest.
- The old FastAPI server + DB + Docker/Render are gone from `main`; recover from the
  `v1-server` tag if you ever need a live API.
- macOS system `python3` is 3.9; use `./.venv` (3.13) for anything real.

## Conventions

- Match surrounding style. Commit only when asked; this repo commits to `main`,
  but confirm before pushing. End commit messages with the Co-Authored-By line.
