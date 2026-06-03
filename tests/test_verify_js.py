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


if __name__ == "__main__":
    unittest.main()
