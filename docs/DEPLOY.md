# Deployment

> 🌏 **English** · [中文](zh/DEPLOY.md)

lucky2049 is a **pure static, server-less** draw engine: drawing, publishing, and verification all
run on GitHub — no standing backend, no database on the critical path.

> Premise: **the draw is deterministic + verifiable**. Any draw can be recomputed from the 144
> hashes for height range `[N*144, N*144+143]` per `SPEC.md`. So the database is only an optional
> local cache — neither publishing nor verification needs the ~170MB `data/database.db`.

---

## Architecture: it all runs on GitHub

- **Drawer (cron)** — [`.github/workflows/refresh-pages.yml`](../.github/workflows/refresh-pages.yml)
  runs [`scripts/extend_pages.py`](../scripts/extend_pages.py) on a schedule (or manual
  `workflow_dispatch`): read the current `index.json` on `gh-pages` → for each **newly confirmed**
  144-block window, fetch the hashes from ≥2 independent sources and require them to agree, continue
  the computation with `verify.py` and chain the commitment → push back to `gh-pages` → Pages
  rebuilds automatically (a draw whose sources disagree is held until they do). **Pure stdlib, no
  DB, no server.**
- **Site** — `index.json` / `head.json` + `web/` (pages) + `static/` (JS/CSS) on `gh-pages`. Hosted
  on GitHub Pages' free CDN; `verify.html` recomputes SHA-256/HMAC in the browser, trusting no server.
- **Head anchoring** — [`.github/workflows/anchor-head.yml`](../.github/workflows/anchor-head.yml)
  weekly timestamps the current head (`head.json`) onto the Bitcoin chain via OpenTimestamps; proofs
  are committed under `anchors/` and served at `/anchors/`.

> A window takes ~144 blocks (≈24h) to mature; the `refresh-pages.yml` cron runs **hourly**, so a
> matured window publishes within the hour (most runs are no-ops, skipped by the diff-guard) — still server-less.

---

## First-time Pages + custom domain

1. Get a snapshot onto `gh-pages` first (see "Rebuild / disaster recovery" below), then enable it
   once in the repo's **Settings > Pages** by selecting the `gh-pages` branch (or
   `gh api -X PUT repos/<owner>/<repo>/pages -f cname=lucky2049.com`).
2. Site address: `https://<owner>.github.io/<repo>/`, or your custom domain.

**Custom domain (lucky2049.com)** — `web/CNAME` holds the domain and is copied into `site/` at
publish time (both `export_static.py` and `refresh-pages.yml` carry it). This step **cannot be
skipped**: the publishers rebuild `gh-pages`, so without `CNAME`, GitHub drops the custom domain on
the next refresh and the site falls back to 404.
- DNS: apex `lucky2049.com` uses 4 A records pointing at `185.199.108–111.153` (an apex can't use
  CNAME); `www` uses a CNAME to `<owner>.github.io`.
- If registering the domain via `gh api ... -f cname=... -F https_enforced=...` reports
  `certificate does not exist yet`, instead commit a `CNAME` file directly to `gh-pages`
  (`gh api -X PUT .../contents/CNAME ... -f branch=gh-pages`); the Pages build then auto-claims the
  domain and issues the certificate.

---

## Day-to-day operations

- **Single publish source**: once the cron is in charge, the `index.json` on `gh-pages` is the
  authoritative snapshot. **Don't also run `scripts/publish-pages.sh` locally** (it force-pushes a
  local-DB export that can clash with the cron's incremental updates). Pick one: use the cron normally.
- **Publish manually**: `gh workflow run refresh-pages.yml` (use it when you've changed
  `web/` / `static/` and want it live immediately).
- `refresh-pages.yml` and the test workflow `tests.yml` are two independent workflows that don't
  affect each other.

---

## Optional: your own Bitcoin Core node as a publish source

Set repository **Actions secrets** and the cron picks your node up as the *preferred* source and
one of the hash-agreement voters (alongside mempool.space / blockstream.info):

- `BITCOIN_RPC_URL` — `https://rpcuser:rpcpassword@your-rpc-host/` (Basic auth is sent as an
  explicit header; URL userinfo alone wouldn't authenticate under urllib).
- `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` — only if the endpoint sits behind a
  Cloudflare Access service token (Zero Trust); the client sends the token headers when set.

Behaviour: secrets unset → explorers only, exactly as before. Node offline or unreachable → its
vote is simply absent; the two explorers still satisfy `MIN_SOURCE_AGREEMENT=2` and publishing
continues. Node still in initial block download → it abstains from the chain tip (stale height
must not delay draws) while still answering hash queries from its header index. A pruned node is
fine — block hashes and header timestamps come from the header index at any height. The cron log
shows which source served the tip (`tip <h> (via core)`) and who agreed on each draw's hashes.

---

## Rebuild / disaster recovery

`index.json` is the system's authoritative snapshot; back it up periodically (it lives in `gh-pages`
git history, and you can also cut a Release). The head is anchored weekly to the Bitcoin chain via
OpenTimestamps by `anchor-head.yml` (see `anchors/`), making the tamper-evidence sturdier. If
`gh-pages` is ever lost, two rebuild paths:

```shell
# A) with a local DB cache: stdlib sqlite3 reads it directly, rebuilding the whole snapshot in seconds
python scripts/export_static.py --out site --db data/database.db

# B) without a DB: start from an empty index.json and let the cron recompute from genesis off the chain
#    (slow; repeatable / raise MAX_NEW_DRAWS)
echo '{"count":0,"head":{},"algo_version":"v1","draws":[]}' > site/index.json
MAX_NEW_DRAWS=500 python scripts/extend_pages.py site/index.json   # rerun until it catches the chain tip
```

`data/database.db` is an **optional local cache** (gitignored, not shipped with the repo); only path
A reads it — the cron never touches the DB.

---

## Verify

```shell
python verify.py <draw_id> --site https://lucky2049.com   # recompute numbers + check the commitment chain (static site works)
python verify.py <draw_id> --source core                   # use your own full node as the source of truth
python verify.py <draw_id> --source db --db data/database.db   # offline, against the local cache
```

Or just open the site's `verify.html` and re-check in the browser with one click. The source of
truth is always the Bitcoin blockchain — anyone can recompute independently with `verify.py` or
`verify.html`, and check history against `head.json`'s head.

> The old container / Render live-service deployment has been removed and archived in git tag
> `v1-server`; recover it if needed.
