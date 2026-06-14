#!/usr/bin/env python3
"""
Guards the in-browser verifier (static/verify.js) by running it under Node and
checking it reproduces the same golden vector as the Python/SPEC implementation.
The JS is what users actually trust on the /verify page, so it must not drift.

Skipped when Node isn't available (the stdlib CI job still runs it on GitHub's
ubuntu runners, which ship Node).
"""
import json
import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

NODE_SCRIPT = r"""
const V = require('./static/verify.js');
const fix = require('./tests/fixtures/draw0_hashes.json');
const abc = V.hex(V.sha256(new TextEncoder().encode("abc")));
const r = V.generate(fix.hashes);
const c0 = V.commitmentFor("0".repeat(64), 0, "v1", r.seedHex, r.front, r.back, 0, 143);
console.log(JSON.stringify({abc, seed: r.seedHex, front: r.front, back: r.back, commit: c0}));
"""


# Exercises V.verifyChain on an honest index and four tampering scenarios. A
# rewritten *middle* draw must be caught even though each draw recomputes fine in
# isolation -- the linkage scan is the only thing that makes the anchored head
# meaningful without a full ~144xN-fetch replay.
NODE_SCRIPT_CHAIN = r"""
const V = require('./static/verify.js');
const Z = "0".repeat(64);
const ok = {draws:[
  {id:0, prev_commitment: Z,    commitment:"c0"},
  {id:1, prev_commitment:"c0",  commitment:"c1"},
  {id:2, prev_commitment:"c1",  commitment:"c2"},
], head:{head:"c2", draw_id:2}};
const clone = o => JSON.parse(JSON.stringify(o));
const midCommit = clone(ok); midCommit.draws[1].commitment = "X";       // breaks draw 2's link
const midPrev   = clone(ok); midPrev.draws[1].prev_commitment = "X";    // breaks draw 1's link
const badHead   = clone(ok); badHead.head.head = "X";                   // head != last commitment
const genesis   = clone(ok); genesis.draws[0].prev_commitment = "X";    // draw 0 not genesis
const bogusId   = clone(ok); bogusId.head.draw_id = 99;                 // head points at a non-existent id
const missHead  = clone(ok); delete missHead.head.head;                 // head.head absent
const truncated = clone(ok); truncated.draws.pop();                     // tail dropped, head still claims it
const noHead    = clone(ok); noHead.head = {};                          // genuinely no head info -> not flagged
console.log(JSON.stringify({
  genesisConst: V.GENESIS_PREV,
  ok: V.verifyChain(ok),
  midCommit: V.verifyChain(midCommit),
  midPrev: V.verifyChain(midPrev),
  badHead: V.verifyChain(badHead),
  genesis: V.verifyChain(genesis),
  bogusId: V.verifyChain(bogusId),
  missHead: V.verifyChain(missHead),
  truncated: V.verifyChain(truncated),
  noHead: V.verifyChain(noHead),
}));
"""


@unittest.skipUnless(NODE, "node not available")
class TestVerifyJs(unittest.TestCase):
    def test_js_matches_golden_vector(self):
        out = subprocess.check_output([NODE, "-e", NODE_SCRIPT], cwd=REPO_ROOT, text=True)
        res = json.loads(out)
        self.assertEqual(res["abc"], "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(res["seed"], "cbc014f38f94d72431f9e1d2f978ff3db74a0be3ffa0e8fcfc1af92818ea324c")
        self.assertEqual(res["front"], [11, 14, 19, 30, 35])
        self.assertEqual(res["back"], [2, 11])
        self.assertEqual(res["commit"], "def4cd38f63acc6f39a9c4dbe0df021c00e219907cbfeb58752be198092b0739")

    def test_chain_linkage_detects_rewrites(self):
        r = json.loads(subprocess.check_output([NODE, "-e", NODE_SCRIPT_CHAIN], cwd=REPO_ROOT, text=True))
        self.assertEqual(r["genesisConst"], "0" * 64)
        self.assertEqual(r["ok"], {"ok": True, "brokenAt": None})
        self.assertEqual(r["midCommit"], {"ok": False, "brokenAt": 2})  # neighbour catches it
        self.assertEqual(r["midPrev"], {"ok": False, "brokenAt": 1})
        self.assertEqual(r["badHead"], {"ok": False, "brokenAt": "head"})
        self.assertEqual(r["genesis"], {"ok": False, "brokenAt": 0})
        # a present-but-decoupled head must NOT silently pass: bogus id, missing
        # head hash, and a truncated tail all break at "head"...
        self.assertEqual(r["bogusId"], {"ok": False, "brokenAt": "head"})
        self.assertEqual(r["missHead"], {"ok": False, "brokenAt": "head"})
        self.assertEqual(r["truncated"], {"ok": False, "brokenAt": "head"})
        self.assertEqual(r["noHead"], {"ok": True, "brokenAt": None})    # ...but no head info at all is fine


if __name__ == "__main__":
    unittest.main()
