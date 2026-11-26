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
const js = fs.readFileSync('./static/page-verify.js', 'utf8');
const script = js.slice(js.indexOf('var V = window.'), js.indexOf('function currentId()'));
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
  // Cache-skew test: initial ./index.json only has draws 0..1 (count=2), then
  // verify(2) retries ./index.json?v=2 once and recovers the newly published draw 2.
  {
    const fresh = snapshot();
    const stale = {count:2, draws:fresh.draws.slice(0, 2), head:{head:fresh.draws[1].commitment, draw_id:1}};
    const nodes = {}; const urls = [];
    const element = () => ({style:{}, appendChild(){}, textContent:'', innerHTML:''});
    const context = {window:{Lucky2049Verify:V},
      document:{getElementById:key => nodes[key] || (nodes[key] = element()), createElement:element},
      fetch:async url => {
        urls.push(url);
        if (url === './index.json') return {ok:true, status:200, json:async () => stale};
        if (url === './index.json?v=2') return {ok:true, status:200, json:async () => fresh};
        if (url === './index.json?v=99') return {ok:true, status:200, json:async () => fresh};
        throw new Error('unexpected network request: ' + url);
      }};
    vm.createContext(context); vm.runInContext(script, context);
    context.fetchHashes = async start => hashes(start / 144);
    await context.verify(2);
    const recoveredVerdict = nodes.verdict.className;
    await context.verify(99);
    await context.verify(99);
    out.cacheSkew = {recoveredVerdict, missingStatus:nodes.status.textContent, urls};
  }
  // Browser OTS head-anchor check: verifies .ots proof of published draw's commitment
  // against Bitcoin block merkle_root from an explorer, and flags merkle_root tampering.
  {
    const idx = snapshot();
    const headRec = {algo_version:'v1', count:3, draw_id:2, head:idx.draws[2].commitment};
    const headText = JSON.stringify(headRec, Object.keys(headRec).sort(), 2) + '\n';
    const headBytes = new TextEncoder().encode(headText);
    const fileDigest = V.sha256(headBytes);
    const salt = new Uint8Array([1, 2, 3, 4]);
    const leaf = V.sha256(new Uint8Array([...fileDigest, ...salt]));
    const mrHex = Array.from(leaf).reverse().map(b => b.toString(16).padStart(2, '0')).join('');
    // OTS_MAGIC + v1(0x01) + sha256(0x08) + fileDigest + append(0xf0,4,salt) + sha256(0x08) + btc(height=100)
    const magic = [0x00,0x4f,0x70,0x65,0x6e,0x54,0x69,0x6d,0x65,0x73,0x74,0x61,0x6d,0x70,0x73,0x00,0x00,0x50,0x72,0x6f,0x6f,0x66,0x00,0xbf,0x89,0xe2,0xe8,0x84,0xe8,0x92,0x94];
    const btcAtt = [0x00, 0x05,0x88,0x96,0x0d,0x73,0xd7,0x19,0x01, 0x01, 100];
    const otsBytes = new Uint8Array([...magic, 0x01, 0x08, ...fileDigest, 0xf0, 0x04, ...salt, 0x08, ...btcAtt]);

    async function runAnchor(reportedMerkle) {
      const nodes = {};
      const element = () => ({style:{}, appendChild(){}, textContent:'', innerHTML:''});
      const context = {window:{Lucky2049Verify:V},
        document:{getElementById:key => nodes[key] || (nodes[key] = element()), createElement:element},
        fetch:async url => {
          if (url === './index.json') return {ok:true, status:200, json:async () => idx};
          if (url === './anchors/000002.head.json') return {ok:true, status:200, arrayBuffer:async () => headBytes.buffer};
          if (url === './anchors/000002.head.json.ots') return {ok:true, status:200, arrayBuffer:async () => otsBytes.buffer};
          if (url === 'https://mempool.space/api/block-height/100') return {ok:true, status:200, text:async () => 'bb'.repeat(32)};
          if (url === 'https://mempool.space/api/block/' + 'bb'.repeat(32)) return {ok:true, status:200, json:async () => ({merkle_root: reportedMerkle})};
          if (url.startsWith('https://blockstream.info/api/')) return {ok:false, status:503};
          throw new Error('unexpected anchor url: ' + url);
        }};
      vm.createContext(context); vm.runInContext(script, context);
      await context.loadIndex();
      await context.verifyAnchor();
      return {badge:nodes.bAnchor.textContent, cls:nodes.bAnchor.className, detail:nodes.vAnchor.textContent};
    }
    out.anchorOk = await runAnchor(mrHex);
    out.anchorBadMerkle = await runAnchor('ff'.repeat(32));
  }
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

    def test_cache_skew_reloads_index_once_with_cache_buster(self):
        skew = self.results["cacheSkew"]
        self.assertEqual(skew["recoveredVerdict"], "verdict pass")
        self.assertIn("./index.json?v=2", skew["urls"])
        # Missing draw 99 busts cache at most once across repeated clicks
        self.assertEqual(skew["urls"].count("./index.json?v=99"), 1)
        self.assertIn("not found", skew["missingStatus"])

    def test_browser_ots_anchor_verification(self):
        self.assertEqual(self.results["anchorOk"]["badge"], "PASS")
        self.assertIn("pass", self.results["anchorOk"]["cls"])
        self.assertIn("100", self.results["anchorOk"]["detail"])
        self.assertEqual(self.results["anchorBadMerkle"]["badge"], "FAIL")
        self.assertIn("fail", self.results["anchorBadMerkle"]["cls"])


if __name__ == "__main__":
    unittest.main()
