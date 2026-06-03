# CLAUDE.md

Guidance for Claude Code working in this repo. Keep it short and current.

## What this is

**lucky2049** is a **draw-engine only** lottery system: it turns 144 consecutive
Bitcoin mainnet block hashes into a Super Lotto result (front 5/35, back 2/12)
via a deterministic algorithm anyone can reproduce. **Scope is deliberately just
the draw** — no prize pool, ticketing, or payout (those are separate projects,
to avoid legal risk). Don't add gambling/business features here.

## ⚠️ The algorithm is FROZEN (read before touching draw logic)

`SPEC.md` is the normative, frozen spec (`ALGO_VERSION = "v1"`). The number
generation in `app/lotto.py` (`generate_lotto_numbers_bitcoin`), the standalone
`verify.py`, and the in-browser `static/verify.js` must stay **bit-for-bit
identical** and match `SPEC.md`.

- Do NOT change the generation logic, game params, seeding (SHA256→HMAC), or the
  commitment formula to "improve" them. Any real change requires a NEW version
  (`v2`, …) that applies to FUTURE draws only; historical draws stay verifiable.
- Golden-vector tests (`tests/test_spec_v1.py`, `tests/test_commitment.py`) are
  **guardrails, not TODOs**. If a change turns them red, the change is wrong.
- The commitment formula lives once in `verify.py` (`commitment_for`) and is
  reused by `app/lotto.py`; keep it that way.

## Run

```shell
pip install -r requirements.txt
uvicorn app.main:app       # serves on :8000 (Docker: docker compose up)
```
Local preview/launch config: `.claude/launch.json` (port 8011). The scheduler
auto-draws every 10 min on startup; DB init runs in the FastAPI lifespan.

## Test / TDD

```shell
make install-dev   # pytest + pytest-cov + pytest-watcher (also pip install -r requirements-dev.txt)
make test          # run once     make watch  # re-run on save (TDD loop)     make cov
python -m unittest discover -s tests   # stdlib-only fallback (core algorithm lock)
```
Tests never touch the real DB: `tests/conftest.py` redirects to a throwaway DB
and offers `db` (clean tables) and `client` (TestClient, no lifespan) fixtures.
Write tests first — see `docs/TDD.md` and the `/healthz` example
(`tests/test_healthz.py`). Deps-gated tests self-skip when pandas/etc. are absent.

## Key files

Code lives in the `app/` package; `verify.py` stays at the repo root (standalone
auditor). Data files live in `data/`.

- `app/lotto.py` — draw engine, manifest, commitment chain (`backfill_commitments`,
  `get_commitment_head`), stats.
- `verify.py` — standalone stdlib verifier (numbers + commitment chain); CLI.
- `static/verify.js` — pure-JS in-browser verifier for the `/verify` page.
- `app/bitcoin.py` — block-hash fetch: Bitcoin Core RPC (primary) + mempool.space
  (fallback). `CONFIRMATIONS` (env `DRAW_CONFIRMATIONS`, default 6) reorg buffer.
- `app/models.py` — SQLModel/SQLite. DB URL via env `LOTTO_DB_URL`; idempotent
  `run_lightweight_migrations()` adds missing columns on startup.
- `app/main.py` — FastAPI routes + scheduler. `SPEC.md` / `README.md` / `docs/TDD.md`.

## Gotchas

- `data/database.db` (~170MB) and `data/blockchain_timeup898560.csv` (~86MB, the
  cold-start seed) are gitignored / large; don't commit DB artifacts.
- After deploying commitment changes, run `lotto.backfill_commitments()` once.
- Anchor `get_commitment_head()` externally (OpenTimestamps / git tag) to make
  history truly tamper-evident — code provides the chain, anchoring is the rest.
- macOS system `python3` is 3.9 with a central bytecode cache; use `./.venv`
  (3.13) for anything real.

## Conventions

- Match surrounding style. Commit only when asked; this repo commits to `main`,
  but confirm before pushing. End commit messages with the Co-Authored-By line.
