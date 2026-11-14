#!/usr/bin/env python3
"""
Locks verify.html's in-browser hash-fetch/assembly path -- the code that selects the
144 block hashes fed into V.generate(), i.e. the page's core trust claim. It lives
inline in web/verify.html (not in static/verify.js), so it's extracted and run under
Node with a stubbed fetch. Asserts: a healthy window assembles all 144 in order from
ONE explorer; a fork/blip restarts the whole window on the next with NO cross-explorer
mixing, including per-height fallback and late in-flight replies. Node + stdlib.
"""
import json
import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
VERIFY_HTML = os.path.join(REPO_ROOT, "web", "verify.html")

# Extract the contiguous fetch-helper block (var BATCH_APIS ... end of fetchHashes,
# just before `var RUN`) and run it with stubbed fetch / $ (progress writes to $()).
NODE_SCRIPT = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const a = html.indexOf('var BATCH_APIS = ['), b = html.indexOf('var RUN = 0');
if (a < 0 || b < 0 || b <= a) { console.error('could not locate the fetch-helper block'); process.exit(2); }
const block = html.slice(a, b);
const $ = () => ({});                                   // progress() sink
const mkApi = (fetch) => new Function('fetch', '$', block + '\nreturn {fetchHashes};')(fetch, $);
const id = (w, ht) => w + ht.toString(16).padStart(63, '0');           // 64-hex, source-tagged
const page = (top, w) => { const o = []; for (let i = 0; i < 10 && top - i >= 0; i++) o.push({ height: top - i, id: id(w, top - i) }); return o; };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const perHeight = handler => async u => {
  const s = String(u);
  if (/\/blocks\/\d+$/.test(s)) throw new Error('no batch');
  const m = s.match(/block-height\/(\d+)$/);
  if (!m) throw new Error('unexpected URL: ' + s);
  return handler(s.includes('mempool') ? 'a' : 'b', +m[1]);
};
(async () => {
  // 1) both explorers healthy -> all 144 from ONE (mempool='a'), correct order
  let f = async (u) => { const m = String(u).match(/\/blocks\/(\d+)$/); return { ok: true, json: async () => page(+m[1], String(u).includes('mempool') ? 'a' : 'b') }; };
  const h1 = await mkApi(f).fetchHashes(0, 143);
  const c1 = h1.length === 144 && h1.every((x, i) => x === id('a', i));

  // 2) mempool drops the top=73 page -> restart whole window on blockstream, NO mix
  f = async (u) => { const s = String(u), m = s.match(/\/blocks\/(\d+)$/), mp = s.includes('mempool'); if (mp && +m[1] === 73) throw new Error('blip'); return { ok: true, json: async () => page(+m[1], mp ? 'a' : 'b') }; };
  const h2 = await mkApi(f).fetchHashes(0, 143);
  const c2 = h2.length === 144 && h2.every((x) => x[0] === 'b');        // all one source, the fallback explorer

  // 3) healthy per-height fallback keeps all 144 slots ordered, with concurrency 8
  let active = 0, maxActive = 0, secondCalls = 0;
  f = perHeight(async (w, h) => {
    if (w === 'b') secondCalls++;
    active++; maxActive = Math.max(maxActive, active);
    await delay(1); active--;
    return { ok: true, text: async () => '  ' + id(w, h).toUpperCase() + '\n' };
  });
  const h3 = await mkApi(f).fetchHashes(5, 148);
  const c3 = h3.length === 144 && h3.every((x, i) => x === id('a', 5 + i))
    && maxActive === 8 && secondCalls === 0;

  // 4) a partial first explorer fails -> refetch EVERY height from the second
  const calls = { a: [], b: [] };
  f = perHeight(async (w, h) => {
    calls[w].push(h);
    if (w === 'a' && h === 12) throw new Error('first explorer dropped a height');
    return { ok: true, text: async () => id(w, h) };
  });
  const h4 = await mkApi(f).fetchHashes(0, 31);
  const c4 = calls.a.length > 1 && calls.b.length === 32
    && h4.length === 32 && h4.every((x, i) => x === id('b', i));

  // 5) complementary partial failures cannot be patched together into a window
  f = perHeight(async (w, h) => {
    if ((w === 'a' && h === 4) || (w === 'b' && h === 5)) throw new Error('partial outage');
    return { ok: true, text: async () => id(w, h) };
  });
  let c5 = false;
  try { await mkApi(f).fetchHashes(0, 15); }
  catch (e) { c5 = /mempool\.space/.test(e.message) && /blockstream\.info/.test(e.message); }

  // 6) a rejected worker must not start the next explorer while old replies can write
  let pendingFirst = 0, overlap = false;
  f = perHeight(async (w, h) => {
    if (w === 'a') {
      if (h === 0) throw new Error('first worker fails immediately');
      pendingFirst++; await delay(15); pendingFirst--;
    } else if (pendingFirst > 0) overlap = true;
    return { ok: true, text: async () => id(w, h) };
  });
  const h6 = await mkApi(f).fetchHashes(0, 15);
  const beforeLateReplies = JSON.stringify(h6);
  await delay(30);
  const c6 = !overlap && pendingFirst === 0 && h6.every((x, i) => x === id('b', i))
    && JSON.stringify(h6) === beforeLateReplies;

  // 7) invalid per-height data has the same whole-window retry semantics as an outage
  f = perHeight(async (w, h) => ({ ok: true, text: async () => w === 'a' && h === 8 ? 'not-a-hash' : id(w, h) }));
  const h7 = await mkApi(f).fetchHashes(0, 15);
  const c7 = h7.every((x, i) => x === id('b', i));

  console.log(JSON.stringify({ c1, c2, c3, c4, c5, c6, c7, len1: h1.length, len2: h2.length, len3: h3.length }));
})().catch((e) => { console.error(e); process.exit(3); });
"""


@unittest.skipUnless(NODE, "node not available")
class TestVerifyFetchJs(unittest.TestCase):
    def test_fetch_assembly_and_anti_mixing(self):
        got = json.loads(subprocess.check_output([NODE, "-e", NODE_SCRIPT, VERIFY_HTML], text=True))
        self.assertEqual(got["len1"], 144)
        self.assertTrue(got["c1"], "healthy window must assemble 144 in order from one explorer")
        self.assertTrue(got["c2"], "a blip must restart the window on the other explorer with no mixing")
        self.assertTrue(got["c3"], "per-height fallback must assemble the window when batched fails")
        self.assertTrue(got["c4"], "a partial per-height outage must restart every height on the next explorer")
        self.assertTrue(got["c5"], "two partial explorers must reject instead of assembling a mixed window")
        self.assertTrue(got["c6"], "all old workers must settle before the next explorer starts")
        self.assertTrue(got["c7"], "invalid hashes must restart the entire window on the next explorer")

    def test_verdict_composition_truth_table(self):
        # Lock the /verify page's whole PASS/FAIL/genesis/unchecked verdict (its core trust output).
        out = subprocess.check_output([NODE, "-e", NODE_SCRIPT_VERDICT, VERIFY_HTML], text=True)
        v = json.loads(out)
        # result reproduces + chain intact -> green PASS
        self.assertEqual(v["genesis"]["state"], "pass"); self.assertIn("genesis commitment verified", v["genesis"]["sub"])
        self.assertEqual(v["normal"]["state"], "pass"); self.assertIn("commitment chain intact", v["normal"]["sub"])
        # predecessor missing -> incomplete verification must not claim PASS or genesis
        self.assertEqual(v["unchecked"]["state"], "err")
        self.assertIn("missing or invalid", v["unchecked"]["sub"])
        self.assertNotIn("genesis", v["unchecked"]["sub"])
        # result mismatch -> FAIL "does NOT match"; numbers ok but chain broken -> FAIL "BROKEN"
        self.assertEqual(v["mismatch"]["state"], "fail"); self.assertIn("does NOT match", v["mismatch"]["text"])
        self.assertEqual(v["broken"]["state"], "fail"); self.assertIn("BROKEN", v["broken"]["text"])


# Extract the pure composeVerdict(resultOk, chainState, id) and exercise the whole truth table.
NODE_SCRIPT_VERDICT = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
// brace-match the function body (robust to reformatting; its strings contain no braces)
const start = html.indexOf('function composeVerdict');
if (start < 0) { console.error('could not locate composeVerdict'); process.exit(2); }
let depth = 0, end = -1, seen = false;
for (let i = html.indexOf('{', start); i >= 0 && i < html.length; i++) {
  if (html[i] === '{') { depth++; seen = true; }
  else if (html[i] === '}' && --depth === 0 && seen) { end = i + 1; break; }
}
if (end < 0) { console.error('could not brace-match composeVerdict'); process.exit(2); }
const composeVerdict = eval('(' + html.slice(start, end) + ')');
console.log(JSON.stringify({
  genesis:   composeVerdict(true,  'pass',      0),
  normal:    composeVerdict(true,  'pass',      5),
  unchecked: composeVerdict(true,  'unchecked', 5),
  mismatch:  composeVerdict(false, 'pass',      5),
  broken:    composeVerdict(true,  'fail',      5),
}));
"""


if __name__ == "__main__":
    unittest.main()
