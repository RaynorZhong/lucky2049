"""Exercise the actual page's loadIndex -> verify -> verdict integration offline."""
import json
import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

NODE_SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const V = require('./static/verify.js');
const html = fs.readFileSync('./web/verify.html', 'utf8');
const script = html.slice(html.indexOf('var V = window.'), html.indexOf('function currentId()'));
const hashes = id => Array.from({length:144}, (_, i) => (id * 144 + i).toString(16).padStart(64, '0'));
function snapshot() {
  const draws = []; let prev = V.GENESIS_PREV;
  for (let id = 0; id < 3; id++) {
    const r = V.generate(hashes(id));
    const commitment = V.commitmentFor(prev, id, 'v1', r.seedHex, r.front, r.back, id * 144, id * 144 + 143);
    draws.push({id, front:r.front, back:r.back, algo_version:'v1', commitment, prev_commitment:prev});
    prev = commitment;
  }
  return {count:3, draws, head:{head:prev, draw_id:2}};
}
async function run(mutate, id=1) {
  const index = snapshot(); if (mutate) mutate(index);
  const nodes = {}; let indexFetches = 0;
  const element = () => ({style:{}, appendChild(){}, textContent:'', innerHTML:''});
  const context = {window:{Lucky2049Verify:V},
    document:{getElementById:key => nodes[key] || (nodes[key] = element()), createElement:element},
    fetch:async url => {
      if (url !== './index.json') throw new Error('unexpected network request');
      indexFetches++; return {json:async () => index};
    }};
  vm.createContext(context); vm.runInContext(script, context);
  context.fetchHashes = async start => hashes(start / 144);
  await context.verify(id); await context.verify(id);
  return {verdict:nodes.verdict.className, sub:nodes.vdSub.textContent,
    chain:nodes.bChain.textContent, result:nodes.bResult.textContent,
    head:nodes.vHead.textContent, indexFetches};
}
(async () => {
  const out = {};
  out.honest = await run(); out.genesis = await run(null, 0);
  out.prevDeclaration = await run(x => x.draws[1].prev_commitment = 'a'.repeat(64));
  out.head = await run(x => x.head.head = 'a'.repeat(64));
  out.otherLink = await run(x => x.draws[0].prev_commitment = 'a'.repeat(64));
  out.commitment = await run(x => x.draws[1].commitment = 'a'.repeat(64));
  out.missingCommitment = await run(x => delete x.draws[1].commitment);
  out.nullDeclaration = await run(x => x.draws[1].prev_commitment = null);
  out.missingPredecessor = await run(x => x.draws.shift());
  // Old scanner accepts paired undefined links; coercing undefined into a new
  // commitment must not turn absent predecessor/declaration evidence into PASS.
  out.undefinedLinks = await run(x => {
    delete x.draws[0].commitment; delete x.draws[1].prev_commitment;
    const r = V.generate(hashes(1));
    x.draws[1].commitment = V.commitmentFor(undefined, 1, 'v1', r.seedHex, r.front, r.back, 144, 287);
    x.draws[2].prev_commitment = x.draws[1].commitment;
  });
  out.newlinePredecessor = await run(x => {
    const prev = 'a'.repeat(64) + '\n', r = V.generate(hashes(1));
    x.draws[0].commitment = prev; x.draws[1].prev_commitment = prev;
    x.draws[1].commitment = V.commitmentFor(prev, 1, 'v1', r.seedHex, r.front, r.back, 144, 287);
    x.draws[2].prev_commitment = x.draws[1].commitment;
  });
  out.olderMissingEvidence = await run(x => {
    delete x.draws[0].commitment; delete x.draws[1].prev_commitment;
  }, 2);
  out.olderMalformedEvidence = await run(x => {
    x.draws[0].commitment = 'invalid'; x.draws[1].prev_commitment = 'invalid';
  }, 2);
  out.duplicateId = await run(x => x.draws.splice(1, 0, JSON.parse(JSON.stringify(x.draws[0]))), 2);
  out.wrongNumbers = await run(x => x.draws[1].front = [1,2,3,4,5]);
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node not available")
class TestVerifyPageIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = json.loads(subprocess.check_output(
            [NODE, "-e", NODE_SCRIPT], cwd=REPO_ROOT, text=True))

    def test_honest_and_genesis_checks_pass_and_reuse_the_snapshot(self):
        for name in ("honest", "genesis"):
            with self.subTest(name=name):
                result = self.results[name]
                self.assertEqual(result["verdict"], "verdict pass")
                self.assertEqual(result["chain"], "PASS")
                self.assertEqual(result["result"], "PASS")
                self.assertEqual(result["indexFetches"], 1)

    def test_any_detected_snapshot_break_fails_the_final_verdict(self):
        for name in ("prevDeclaration", "head", "otherLink", "commitment"):
            with self.subTest(name=name):
                result = self.results[name]
                self.assertIn("BROKEN", result["head"])
                self.assertEqual(result["result"], "PASS")
                self.assertEqual(result["verdict"], "verdict fail")
                self.assertEqual(result["chain"], "FAIL")
                self.assertNotIn("chain intact", result["sub"])

    def test_missing_evidence_cannot_be_coerced_into_success(self):
        for name in ("missingCommitment", "nullDeclaration", "missingPredecessor", "undefinedLinks",
                     "newlinePredecessor", "olderMissingEvidence", "olderMalformedEvidence", "duplicateId"):
            with self.subTest(name=name):
                result = self.results[name]
                self.assertNotEqual(result["verdict"], "verdict pass")
                self.assertNotEqual(result["chain"], "PASS")
                self.assertNotIn("chain intact", result["sub"])

    def test_wrong_numbers_still_fail(self):
        result = self.results["wrongNumbers"]
        self.assertEqual(result["verdict"], "verdict fail")
        self.assertEqual(result["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()
