#!/usr/bin/env python3
"""
Locks the homepage's economic-security math -- the headline B/p "safe prize ceiling"
disclosure -- and the next-draw ETA window. Both are inline in web/index.html and
were otherwise untested: a wrong odds denominator or a stale subsidy would publish a
misleading safety claim without turning a test red. The self-contained JACKPOT_COMBOS
expression is run under Node and cross-checked against an independent Python
re-derivation; the ETA formula is asserted structurally. Node + stdlib only.
"""
import json
import math
import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
INDEX = os.path.join(REPO_ROOT, "web", "index.html")

# Pull the self-contained JACKPOT_COMBOS IIFE + the subsidy constant out of the page
# and evaluate them (no DOM involved), so the published math is what actually runs.
NODE_SCRIPT = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const mC = html.match(/var JACKPOT_COMBOS = (\(function[\s\S]*?\}\)\(\));/);
const mB = html.match(/var BLOCK_SUBSIDY_BTC = ([\d.]+);/);
const combos = eval(mC[1]);                                  // C(35,5) * C(12,2)
const subsidy = parseFloat(mB[1]);
const usd = 100000;                                          // sample BTC price
const ceiling = (subsidy * usd) * combos;                   // W_max = B / p
console.log(JSON.stringify({ combos: combos, subsidy: subsidy, ceiling: ceiling }));
"""


class TestIndexEconBound(unittest.TestCase):
    def setUp(self):
        with open(INDEX) as f:
            self.html = f.read()

    @unittest.skipUnless(NODE, "node not available")
    def test_jackpot_combos_and_ceiling_match_python(self):
        got = json.loads(subprocess.check_output([NODE, "-e", NODE_SCRIPT, INDEX], text=True))
        combos = math.comb(35, 5) * math.comb(12, 2)         # 324632 * 66
        self.assertEqual(combos, 21425712)                   # SPEC odds: 5 of 35 + 2 of 12
        self.assertEqual(got["combos"], combos)              # the page computes the right denominator p
        self.assertEqual(got["subsidy"], 3.125)              # post-2024-halving block subsidy B
        self.assertAlmostEqual(got["ceiling"], 3.125 * 100000 * combos, places=0)  # ceiling = B / p

    def test_next_draw_eta_targets_window_end_plus_6_confirmations(self):
        # ETA must target the LAST block of the next 144-block window + 6 confirmations.
        self.assertRegex(self.html, r"nextId \* 144 \+ 143 \+ 6")


if __name__ == "__main__":
    unittest.main()
