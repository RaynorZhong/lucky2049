#!/usr/bin/env python3
"""
Locks the external trigger (ops/trigger-worker): a Cloudflare Worker cron that asks
GitHub to run refresh-pages.yml hourly and anchor-head.yml daily, because GitHub throttles their
`schedule:`. The Worker holds a credential, so this pins exactly what it sends and what
it refuses to do: one authenticated workflow_dispatch POST, one retry on 5xx/429/network
only, a loud failure otherwise, no token in error text, and no HTTP-triggered dispatch.
Runs the real module under Node with a stubbed fetch; config is checked as text. Stdlib.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER_DIR = os.path.join(REPO_ROOT, "ops", "trigger-worker")
WORKER = os.path.join(WORKER_DIR, "src", "index.js")
NODE = shutil.which("node")

NODE_SCRIPT = r"""
const { pathToFileURL } = require('url');
(async () => {
  const mod = await import(pathToFileURL(process.argv[1]).href);
  const TOKEN = 'tok_SECRET_do_not_leak';
  const env = { GITHUB_TOKEN: TOKEN, GITHUB_REPO: 'o/r', WORKFLOW_FILE: 'refresh-pages.yml',
                ANCHOR_WORKFLOW_FILE: 'anchor-head.yml', ANCHOR_CRON:'35 2 * * *', WORKFLOW_REF: 'main' };
  const mk = (seq) => { const calls = []; let i = 0;
    const f = async (url, init) => { calls.push({ url, init }); const s = seq[Math.min(i++, seq.length - 1)];
      if (s === 'net') throw new Error('boom'); return { status: s, text: async () => 'body ' + s }; };
    return { f, calls }; };
  const run = async (seq, e) => { const { f, calls } = mk(seq);
    try { const status = await mod.dispatch(e || env, f, 0); return { ok: true, status, n: calls.length, calls }; }
    catch (err) { return { ok: false, msg: String(err.message), n: calls.length, calls }; } };
  const out = {};
  const s = await run([204]);
  out.success = { ok: s.ok, status: s.status, n: s.n, url: s.calls[0].url, method: s.calls[0].init.method,
                  headers: s.calls[0].init.headers, body: s.calls[0].init.body };
  for (const [k, seq] of [['retry5xx', [502, 204]], ['retry429', [429, 204]], ['retryNet', ['net', 204]],
                          ['fail5xx', [500, 500]], ['failNet', ['net', 'net']], ['fail401', [401]],
                          ['fail403', [403]], ['fail404', [404]], ['fail422', [422]]]) {
    const r = await run(seq); out[k] = { ok: r.ok, n: r.n, msg: r.msg || '', leaks: (r.msg || '').includes(TOKEN) };
  }
  const noTok = await run([204], { ...env, GITHUB_TOKEN: '' });
  out.noToken = { ok: noTok.ok, n: noTok.n, msg: noTok.msg };
  // the cron entry point uses the global fetch; the HTTP entry point must never dispatch
  const scheduled = async (event, e=env, status=204) => {
    const calls=[], logged=[]; const log=console.log;
    globalThis.fetch = async (url, init) => { calls.push({url,init}); return {status, text:async ()=>'response'}; };
    console.log=(...a)=>logged.push(a.join(' '));
    let error='';
    try { await mod.default.scheduled(event,e,{}); }
    catch (err) { error=String(err.message); }
    finally { console.log=log; }
    return {calls, logged:logged.join('\n'), error, leaks:(error+'\n'+logged.join('\n')).includes(TOKEN)};
  };
  out.scheduled = await scheduled({cron:'50 * * * *'});
  out.anchorScheduled = await scheduled({cron:'35 2 * * *'});
  out.scheduledFail = await scheduled({cron:'50 * * * *'},env,401);
  out.anchorScheduledFail = await scheduled({cron:'35 2 * * *'},env,401);
  out.noCron = await scheduled({});
  out.unknownCron = await scheduled({cron:'* * * * *'});
  out.prototypeCron = await scheduled({cron:'toString'});
  out.noAnchorWorkflow = await scheduled({cron:'35 2 * * *'},{...env,ANCHOR_WORKFLOW_FILE:''});
  out.noAnchorCron = await scheduled({cron:'35 2 * * *'},{...env,ANCHOR_CRON:''});
  const noAnchorCron = {...env}; delete noAnchorCron.ANCHOR_CRON;
  out.absentAnchorCron = await scheduled({cron:'35 2 * * *'},noAnchorCron);
  out.hourlyWithoutAnchor = await scheduled({cron:'50 * * * *'},
    {...env,ANCHOR_CRON:'',ANCHOR_WORKFLOW_FILE:''});
  const datedCron = '34 13 14 11 *', datedEnv = {...env,ANCHOR_CRON:datedCron};
  out.datedAnchor = await scheduled({cron:datedCron},datedEnv);
  out.oldAnchorCron = await scheduled({cron:'35 2 * * *'},datedEnv);
  out.envUnchanged = env.WORKFLOW_FILE === 'refresh-pages.yml';
  let globalCalls = 0;
  globalThis.fetch = async () => { globalCalls++; return {status:204}; };
  const resp = await mod.default.fetch(new Request('https://example.invalid/'), env, {});
  out.http = { status: resp.status, dispatched: globalCalls };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(3); });
"""


@unittest.skipUnless(NODE, "node not available")
class TestTriggerWorkerBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = json.loads(subprocess.check_output([NODE, "-e", NODE_SCRIPT, WORKER], text=True))

    def test_sends_exactly_one_authenticated_dispatch(self):
        s = self.r["success"]
        self.assertTrue(s["ok"]); self.assertEqual(s["n"], 1); self.assertEqual(s["status"], 204)
        self.assertEqual(s["url"], "https://api.github.com/repos/o/r/actions/workflows/refresh-pages.yml/dispatches")
        self.assertEqual(s["method"], "POST")
        self.assertEqual(json.loads(s["body"]), {"ref": "main"})
        h = s["headers"]
        self.assertEqual(h["Authorization"], "Bearer tok_SECRET_do_not_leak")
        self.assertEqual(h["Accept"], "application/vnd.github+json")
        self.assertTrue(h["User-Agent"])          # GitHub rejects API calls without a User-Agent

    def test_retries_once_on_transient_failures_only(self):
        for k in ("retry5xx", "retry429", "retryNet"):
            self.assertTrue(self.r[k]["ok"], k); self.assertEqual(self.r[k]["n"], 2, k)
        for k in ("fail5xx", "failNet"):           # gives up after ONE retry
            self.assertFalse(self.r[k]["ok"], k); self.assertEqual(self.r[k]["n"], 2, k)
        for k in ("fail401", "fail403", "fail404", "fail422"):   # a bad token/target is never retried
            self.assertFalse(self.r[k]["ok"], k); self.assertEqual(self.r[k]["n"], 1, k)

    def test_failures_are_loud_and_never_leak_the_token(self):
        for k, v in self.r.items():
            if isinstance(v, dict) and "leaks" in v:
                self.assertFalse(v["leaks"], k + " error text contains the token")
        self.assertIn("HTTP 401", self.r["fail401"]["msg"])
        self.assertIn("rotate", self.r["fail401"]["msg"])        # points the operator at the fix

    def test_missing_token_fails_before_any_request(self):
        self.assertFalse(self.r["noToken"]["ok"]); self.assertEqual(self.r["noToken"]["n"], 0)
        self.assertIn("GITHUB_TOKEN", self.r["noToken"]["msg"])

    def test_each_cron_dispatches_only_its_own_workflow(self):
        for key, cron, workflow in (("scheduled", "50 * * * *", "refresh-pages.yml"),
                                    ("anchorScheduled", "35 2 * * *", "anchor-head.yml"),
                                    ("datedAnchor", "34 13 14 11 *", "anchor-head.yml"),
                                    ("hourlyWithoutAnchor", "50 * * * *", "refresh-pages.yml")):
            with self.subTest(cron=cron):
                result = self.r[key]
                self.assertEqual(result["error"], "")
                self.assertEqual(len(result["calls"]), 1)
                call = result["calls"][0]
                self.assertEqual(call["url"], "https://api.github.com/repos/o/r/actions/workflows/" + workflow + "/dispatches")
                self.assertEqual(call["init"]["method"], "POST")
                self.assertEqual(json.loads(call["init"]["body"]), {"ref": "main"})
                self.assertEqual(call["init"]["headers"]["Authorization"], "Bearer tok_SECRET_do_not_leak")
                self.assertIn("dispatched " + workflow, result["logged"])
                self.assertIn(cron, result["logged"])
        self.assertTrue(self.r["envUnchanged"])

    def test_failed_dispatch_rejects_both_cron_handlers(self):
        for key in ("scheduledFail", "anchorScheduledFail"):
            with self.subTest(key=key):
                result = self.r[key]
                self.assertEqual(len(result["calls"]), 1)
                self.assertIn("HTTP 401", result["error"])

    def test_unknown_cron_or_missing_binding_fails_before_any_request(self):
        for key in ("noCron", "unknownCron", "prototypeCron", "noAnchorWorkflow", "noAnchorCron",
                    "absentAnchorCron", "oldAnchorCron"):
            with self.subTest(key=key):
                result = self.r[key]
                self.assertEqual(result["calls"], [])
                self.assertTrue(result["error"])
        self.assertIn("ANCHOR_WORKFLOW_FILE", self.r["noAnchorWorkflow"]["error"])
        self.assertIn("ANCHOR_CRON", self.r["noAnchorCron"]["error"])
        self.assertIn("ANCHOR_CRON", self.r["absentAnchorCron"]["error"])

    def test_http_never_dispatches(self):
        self.assertEqual(self.r["http"], {"status": 404, "dispatched": 0})


class TestTriggerWorkerConfig(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(WORKER_DIR, "wrangler.toml")) as f:
            self.toml = f.read()

    def test_no_public_surface_and_the_two_expected_crons(self):
        self.assertRegex(self.toml, r"(?m)^workers_dev\s*=\s*false")
        self.assertRegex(self.toml, r"(?m)^preview_urls\s*=\s*false")
        self.assertNotRegex(self.toml, r"(?m)^\s*\[*\s*routes?\b")     # no `route =`, `routes =`, or [[routes]]
        crons = re.search(r'(?m)^crons\s*=\s*(\[[^\n]+\])', self.toml)
        self.assertIsNotNone(crons)
        self.assertEqual(json.loads(crons.group(1)), ["50 * * * *", "35 2 * * *"])
        anchor_cron = re.search(r'(?m)^ANCHOR_CRON\s*=\s*"([^"]+)"', self.toml)
        self.assertIsNotNone(anchor_cron, "anchor cron must be configured for the scheduled handler")
        self.assertEqual(anchor_cron.group(1), json.loads(crons.group(1))[1])

    def test_targets_a_dispatchable_workflow_and_holds_no_secret(self):
        repo = re.search(r'(?m)^GITHUB_REPO\s*=\s*"([^"]+)"', self.toml).group(1)
        workflows = []
        for key in ("WORKFLOW_FILE", "ANCHOR_WORKFLOW_FILE"):
            match = re.search(r'(?m)^' + key + r'\s*=\s*"([^"]+)"', self.toml)
            self.assertIsNotNone(match, key + " binding must be configured")
            workflows.append(match.group(1))
        self.assertEqual(repo, "RaynorZhong/lucky2049")
        self.assertRegex(self.toml, r'(?m)^WORKFLOW_REF\s*=\s*"main"')   # only ever publish from main
        self.assertEqual(workflows, ["refresh-pages.yml", "anchor-head.yml"])
        for wf in workflows:
            with open(os.path.join(REPO_ROOT, ".github", "workflows", wf)) as f:
                text = f.read()
            self.assertIn("workflow_dispatch", text, wf + " must accept workflow_dispatch")
            self.assertRegex(text, r'(?m)^  schedule:\n    - cron: "[^"]+"')
        self.assertNotRegex(self.toml, r"(?m)^GITHUB_TOKEN\s*=")       # the token is a Worker secret
        with open(WORKER) as f:
            worker_src = f.read()
        for blob in (self.toml, worker_src):
            self.assertNotRegex(blob, r"github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}")


if __name__ == "__main__":
    unittest.main()
