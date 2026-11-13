/*
 * lucky2049 — external hourly trigger for the refresh-pages workflow.
 *
 * GitHub throttles `schedule:` events on this repo hard (the "hourly" cron was firing
 * ~4x/day by Nov 2026), which delayed draw publication by hours and left the homepage
 * source line "stale". A `workflow_dispatch` is NOT throttled — it runs immediately — so
 * this Cloudflare Worker's cron simply asks GitHub to run the workflow once an hour.
 *
 * It does nothing else: no draw logic, no data, no public HTTP surface. The workflow's
 * own `schedule:` stays as a fallback, and its concurrency group makes an overlapping
 * trigger harmless.
 *
 * Config (wrangler.toml [vars]): GITHUB_REPO, WORKFLOW_FILE, WORKFLOW_REF.
 * Secret (wrangler secret put): GITHUB_TOKEN — a fine-grained PAT scoped to this one
 * repository with "Actions: Read and write" and nothing else. See docs/DEPLOY.md.
 */

const API = "https://api.github.com";
const RETRY_DELAY_MS = 5000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// One dispatch attempt. Returns { ok, status, retryable, detail }; never includes the token.
async function attempt(env, fetchImpl) {
  const url = `${API}/repos/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW_FILE}/dispatches`;
  let resp;
  try {
    resp = await fetchImpl(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "lucky2049-trigger-worker",   // GitHub's API rejects requests without one
      },
      body: JSON.stringify({ ref: env.WORKFLOW_REF || "main" }),
    });
  } catch (e) {
    return { ok: false, status: 0, retryable: true, detail: `network error: ${e && e.message}` };
  }
  if (resp.status >= 200 && resp.status < 300) return { ok: true, status: resp.status };
  let detail = "";
  try { detail = (await resp.text()).slice(0, 300); } catch (e) { /* body is optional */ }
  // 5xx and 429 are worth one retry; 4xx (bad/expired token, wrong repo/workflow) are not.
  return { ok: false, status: resp.status, retryable: resp.status >= 500 || resp.status === 429, detail };
}

// Ask GitHub to run the workflow. Throws on failure so Cloudflare records the cron
// invocation as an error (visible in the dashboard / `wrangler tail`).
export async function dispatch(env, fetchImpl = fetch, delayMs = RETRY_DELAY_MS) {
  for (const key of ["GITHUB_TOKEN", "GITHUB_REPO", "WORKFLOW_FILE"]) {
    if (!env || !env[key]) throw new Error(`missing ${key} — see docs/DEPLOY.md (external trigger)`);
  }
  let r = await attempt(env, fetchImpl);
  if (!r.ok && r.retryable) {
    await sleep(delayMs);
    r = await attempt(env, fetchImpl);
  }
  if (!r.ok) {
    const hint = r.status === 401
      ? " (token expired or revoked — rotate it, see docs/DEPLOY.md)"
      : r.status === 403
        ? " (token lacks 'Actions: write', or GitHub rate-limited the request — check the body; see docs/DEPLOY.md)"
        : r.status === 404 ? " (wrong GITHUB_REPO/WORKFLOW_FILE, or the token can't see the repo)" : "";
    throw new Error(`dispatch of ${env.WORKFLOW_FILE} failed: HTTP ${r.status}${hint} ${r.detail || ""}`.trim());
  }
  return r.status;
}

export default {
  async scheduled(event, env, ctx) {
    const status = await dispatch(env);
    console.log(`dispatched ${env.WORKFLOW_FILE} on ${env.GITHUB_REPO}@${env.WORKFLOW_REF || "main"} (HTTP ${status})`);
  },
  // No public trigger: the Worker has no route and workers.dev is disabled in wrangler.toml.
  // If it is ever reached over HTTP anyway, it must not dispatch.
  async fetch() {
    return new Response("lucky2049 trigger worker: cron only\n", { status: 404 });
  },
};
