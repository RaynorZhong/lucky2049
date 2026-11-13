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
  daily timestamps the current head (`head.json`) onto the Bitcoin chain via OpenTimestamps; proofs
  are committed under `anchors/` and served at `/anchors/<id>.head.json(.ots)` (no directory
  listing; the list of anchored ids is the [`anchors/`](../anchors/) directory on GitHub). The
  workflow is two jobs on purpose: `stamp` runs the PyPI-installed OpenTimestamps client with a
  read-only token and a hash-locked install ([`ops/anchor-requirements.txt`](../ops/anchor-requirements.txt));
  `commit` holds the write token, runs no third-party code, and admits the stamped files only
  through [`scripts/accept_anchors.py`](../scripts/accept_anchors.py).

> A window takes ~144 blocks (≈24h) to mature; `refresh-pages.yml` is meant to run **hourly**, so a
> matured window publishes within the hour (runs without a new draw just refresh the `status.json`
> source-health heartbeat, committed as "Refresh source health") — still server-less.
>
> GitHub does not honour the hourly `schedule:` reliably (by Nov 2026 it fired ~4×/day on this
> repo), so the hourly cadence comes from a tiny external trigger — see
> [External trigger](#external-trigger-cloudflare-worker) below. The workflow's own `schedule:`
> stays as the fallback, and the trigger carries no draw logic: everything that decides or
> publishes a draw still runs on GitHub.

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
- **Verify the domain** (once): GitHub account **Settings → Pages → Add a domain** → enter
  `lucky2049.com` → create the `_github-pages-challenge-<user>` TXT record it shows (DNS only) and
  leave it in place. Until it is verified, another GitHub account could claim the domain if this
  Pages site were ever unpublished while DNS still pointed at GitHub. Check with
  `gh api repos/<owner>/<repo>/pages --jq .protected_domain_state` (expect `verified`).

---

## Day-to-day operations

- **Single publish source**: once the cron is in charge, the `index.json` on `gh-pages` is the
  authoritative snapshot. **Don't also run `scripts/publish-pages.sh` locally** (it force-pushes a
  local-DB export that can clash with the cron's incremental updates). Pick one: use the cron normally.
- **Publish manually**: `gh workflow run refresh-pages.yml` (use it when you've changed
  `web/` / `static/` and want it live immediately).
- `refresh-pages.yml` and the test workflow `tests.yml` are two independent workflows that don't
  affect each other.
- **Time budgets**: the drawer fetches its sources concurrently, gives each `SOURCE_BUDGET_S`
  (default 300 s) per window, skips a source whose tip probe failed, and starts no new draw or
  audit after `RUN_BUDGET_S` (default 900 s) — so one slow source can cost a vote but not the run,
  which normally finishes well inside the 30-minute job timeout (worst case ≈ `RUN_BUDGET_S` + one
  `SOURCE_BUDGET_S` + a timestamp lookup). The price: a source that failed its probe casts no hash
  vote that run, so with three sources a second one failing in the same run holds the draw until
  the next hourly run — self-healing, never a wrong commit.
- **A red `anchor-head` run saying `REJECTED the stamp job's output`**: the commit job refused the
  stamped files and committed nothing. `… is not a published draw` minutes after a fresh draw is
  just a stale CDN copy of `index.json` (it retries for a few minutes first) — the next daily run
  heals it. Any other reason means the stamp job produced something it should not have: read the
  log before re-running. The gate confines the stamper to `anchors/` and binds each proof to its
  published head, but it does not check a proof against the Bitcoin chain — run `ots verify` on a
  recent proof now and then (see [`anchors/README.md`](../anchors/README.md)).

---

## Monitoring (dead-man's switch)

A failed run notifies you; a run that never happens does not. The trigger token expiring, the
schedule being disabled, or a GitHub outage would all be silent. So the last step of each workflow
can ping an external monitor that alerts when pings **stop**. Both are no-ops until you set the
secret:

| Secret (repo → Settings → Secrets and variables → Actions) | Pinged by | Suggested schedule on the monitor |
|---|---|---|
| `HEARTBEAT_URL` | `refresh-pages.yml`, after a fully healthy run | period 1 h, grace 2 h |
| `ANCHOR_HEARTBEAT_URL` | `anchor-head.yml`, after the proofs are committed and none is stuck pending | period 1 day, grace 12 h |

Any "cron job monitoring" service with a plain ping URL works (e.g. a free
[healthchecks.io](https://healthchecks.io) check). With a 2-hour grace on the hourly ping, losing
the external trigger alone is enough to alert, because GitHub's own schedule fires only every few
hours. The URL is a secret because anyone who knows it can keep the check green.

---

## External trigger (Cloudflare Worker)

[`ops/trigger-worker/`](../ops/trigger-worker/) is a Cloudflare Worker with a cron
(`50 * * * *`, UTC) that sends one `workflow_dispatch` for `refresh-pages.yml` every hour. A
dispatch runs immediately, unlike GitHub's throttled `schedule:` events. The Worker has no public
URL (`workers_dev = false`, no routes) and does nothing but that one API call; the workflow's
concurrency group makes an overlap with the fallback schedule harmless.

**Deploy (once):**

1. Create a **fine-grained personal access token** (GitHub → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens): *Repository access* = only this repository; *Repository permissions* →
   **Actions: Read and write** (Metadata: Read-only is added automatically); nothing else. Pick an
   expiry of a year or less (GitHub also offers "No expiration" — avoid it: an expiring token
   limits the damage if it leaks) and put a reminder in your calendar.
2. From `ops/trigger-worker/`:
   ```shell
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN     # paste the token; it is stored only as a Worker secret
   ```
3. Check after the next `:50`: `gh run list --workflow=refresh-pages.yml -L 3` shows a
   `workflow_dispatch` run, and `npx wrangler tail` (or the Worker's logs in the dashboard) prints
   `dispatched refresh-pages.yml …`.

**Rotate the token** (before it expires, or if it may have leaked): create a new one as in step 1,
run `npx wrangler secret put GITHUB_TOKEN` again (no redeploy needed), then delete the old token on
GitHub.

**If it stops:** an expired/revoked token makes every invocation fail with `HTTP 401 … rotate it`
in the Worker's logs. Nothing breaks — publishing falls back to GitHub's own schedule — but the
cadence drops again and the homepage source line starts reading "stale" (>4 h since a refresh).

**If the token leaks:** it cannot push code or read secrets, but it is not harmless. A
fine-grained token can't be narrowed to one workflow or one ref, so "Actions: Read and write" lets
its holder dispatch **any** workflow in this repository at **any** ref, re-run or cancel runs,
delete run logs/artifacts/caches, and disable workflows. The sharp edge is the archived
`v1-server` tag: its old `refresh-pages.yml` is still dispatchable and force-pushes `gh-pages`
from a single hash source. The next run from `main` restores the site, but `gh-pages` history
would be gone. So: revoke the token at once on any suspicion, check that nothing was disabled
(`gh workflow list --all`), and re-run `refresh-pages` from `main`.

**Recommended hardening (once, in the repo's Settings → Rules → Rulesets):** block force pushes
and deletion on `gh-pages` and `main`. Every workflow on `main` uses a normal push, so nothing
legitimate is affected, and the archived publisher's `push -f` can no longer rewrite the site.
(`scripts/publish-pages.sh` also force-pushes — it is the disaster-recovery path; lift the rule
temporarily if you ever need it.)

**Remove:** `npx wrangler delete` from `ops/trigger-worker/`, and delete the token on GitHub.

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
shows each source's probe (`source core: ok tip=<h> (12ms)`), the quorum tip and which source led
it (`tip <h> (quorum of 2; leading source core)`), and who agreed on each draw's hashes.

**Monitoring your node:** every hourly run probes all sources and publishes the results to
[`status.json`](https://lucky2049.com/status.json) (per source: ok / tip / latency / error), also
rendered as the homepage "Sources (last refresh)" strip — green means your Core RPC answered on
the last refresh. Schema in `docs/SCHEMA.md`.

---

## Rebuild / disaster recovery

`index.json` is the system's authoritative snapshot; back it up periodically (it lives in `gh-pages`
git history, and you can also cut a Release). The head is anchored daily to the Bitcoin chain via
OpenTimestamps by `anchor-head.yml` (see `anchors/`), making the tamper-evidence sturdier. If
`gh-pages` is ever lost, two rebuild paths:

```shell
# A) with a local DB cache: stdlib sqlite3 reads it directly, rebuilding the whole snapshot in seconds
python scripts/export_static.py --out site --db data/database.db

# B) without a DB: start from an empty index.json and recompute from genesis off the chain
#    (slow: each invocation stops starting new draws after RUN_BUDGET_S and only then writes
#    index.json -- so loop it rather than raising the budget; every pass is a checkpoint)
echo '{"count":0,"head":{},"algo_version":"v1","draws":[]}' > site/index.json
while MAX_NEW_DRAWS=500 python scripts/extend_pages.py site/index.json | tee /dev/stderr | grep -q 'run budget'; do :; done
```

`data/database.db` is an **optional local cache** (gitignored, not shipped with the repo); only path
A reads it — the cron never touches the DB. The cache is only as new as the last time you filled it,
so after path A run `python scripts/extend_pages.py site/index.json` (repeat until it reports
`ADDED=0`) to catch up from the cache's last draw to the chain tip before publishing.

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
