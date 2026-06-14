#!/usr/bin/env python3
"""
Locks verify.html's in-browser hash-fetch/assembly path -- the code that selects the
144 block hashes fed into V.generate(), i.e. the page's core trust claim. It lives
inline in web/verify.html (not in static/verify.js), so it's extracted and run under
Node with a stubbed fetch. Asserts: a healthy window assembles all 144 in order from
ONE explorer; a fork/blip on one explorer restarts the whole window on the next with
NO cross-explorer mixing (the anti-Frankenstein guarantee); and when neither batched
explorer can serve it, the per-height fallback still assembles the window. Node + stdlib.
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
(async () => {
  // 1) both explorers healthy -> all 144 from ONE (mempool='a'), correct order
  let f = async (u) => { const m = String(u).match(/\/blocks\/(\d+)$/); return { ok: true, json: async () => page(+m[1], String(u).includes('mempool') ? 'a' : 'b') }; };
  const h1 = await mkApi(f).fetchHashes(0, 143);
  const c1 = h1.length === 144 && h1.every((x, i) => x === id('a', i));

  // 2) mempool drops the top=73 page -> restart whole window on blockstream, NO mix
  f = async (u) => { const s = String(u), m = s.match(/\/blocks\/(\d+)$/), mp = s.includes('mempool'); if (mp && +m[1] === 73) throw new Error('blip'); return { ok: true, json: async () => page(+m[1], mp ? 'a' : 'b') }; };
  const h2 = await mkApi(f).fetchHashes(0, 143);
  const c2 = h2.length === 144 && h2.every((x) => x[0] === 'b');        // all one source, the fallback explorer

  // 3) neither batched explorer works -> per-height fallback assembles the window
  f = async (u) => { const s = String(u); if (/\/blocks\/\d+$/.test(s)) throw new Error('no batch'); const m = s.match(/block-height\/(\d+)$/); if (m) return { ok: true, text: async () => id('a', +m[1]) }; throw new Error('x'); };
  const h3 = await mkApi(f).fetchHashes(5, 8);
  const c3 = h3.length === 4 && h3.every((x, i) => x === id('a', 5 + i));

  console.log(JSON.stringify({ c1, c2, c3, len1: h1.length, len2: h2.length, len3: h3.length }));
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


if __name__ == "__main__":
    unittest.main()
