#!/usr/bin/env python3
"""
Locks the verifiable-randomness demo (randomness.py <-> static/randomness.js):
golden vectors for the Python reference, an unbiasedness/fairness sanity check,
and a Node parity run proving the in-browser JS reproduces the Python byte-for-byte
(the page is what users actually run, so it must not drift). Stdlib + Node only.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import randomness as R  # noqa: E402

NODE = shutil.which("node")
GENESIS = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"  # Bitcoin block 0
HASH2 = "deadbeef" * 8


class TestRandomnessGolden(unittest.TestCase):
    def test_golden_vectors(self):
        self.assertEqual(R.seed_for(GENESIS).hex(),
                         "09f663de96be771f50cab5ded00256ffe63773e2eaa9a604092951cc3d7c6621")
        self.assertEqual("".join(R.coin_flips(GENESIS, 24)), "THHHTTHTHHHHHHHHTTTTHHHH")
        self.assertEqual("".join(map(str, R.dice_rolls(GENESIS, 16))), "3356122314345462")

    def test_coin_is_the_msb_first_bits_of_the_stream(self):
        import hashlib
        import hmac
        b0 = hmac.new(R.seed_for(GENESIS), b"coin:0", hashlib.sha256).digest()[0]
        expect = "".join("H" if (b0 >> (7 - i)) & 1 else "T" for i in range(8))
        self.assertEqual("".join(R.coin_flips(GENESIS, 8)), expect)

    def test_dice_unbiased_in_range(self):
        rolls = R.dice_rolls(GENESIS, 3000)
        self.assertTrue(all(1 <= r <= 6 for r in rolls))
        c = Counter(rolls)
        self.assertEqual(set(c), {1, 2, 3, 4, 5, 6})
        for v in range(1, 7):
            self.assertGreater(c[v], 3000 / 6 * 0.8)  # within ~20% of 1/6 (loose, deterministic)

    def test_deterministic_and_prefix_stable(self):
        # same hash -> same outcomes; asking for more never changes the earlier ones
        self.assertEqual(R.coin_flips(GENESIS, 50)[:30], R.coin_flips(GENESIS, 30))
        self.assertEqual(R.dice_rolls(GENESIS, 50)[:30], R.dice_rolls(GENESIS, 30))
        self.assertNotEqual(R.coin_flips(GENESIS, 30), R.coin_flips(HASH2, 30))  # different seed

    def test_bad_hash_rejected(self):
        for bad in ["", "xyz", "0" * 63, "0" * 65, "g" * 64, "  " + "0" * 64 + "x"]:
            with self.assertRaises(ValueError):
                R.seed_for(bad)

    def test_normalizes_case_and_whitespace(self):
        self.assertEqual(R.coin_flips("  " + GENESIS.upper() + "\n", 20), R.coin_flips(GENESIS, 20))

    def test_invalid_sides_rejected(self):
        for s in (0, 1, 257, -3):
            with self.assertRaises(ValueError):
                R.dice_rolls(GENESIS, 5, sides=s)


NODE_SCRIPT = r"""
const V = require('./static/verify.js');
const R = require('./static/randomness.js');
const cases = [
  ["000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f", 24, 16],
  ["deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", 33, 25],
];
console.log(JSON.stringify(cases.map(function (c) {
  return { seed: V.hex(R.seedFor(c[0])), coins: R.coinFlips(c[0], c[1]).join(""),
           dice: R.diceRolls(c[0], c[2]).join(",") };
})));
"""


@unittest.skipUnless(NODE, "node not available")
class TestRandomnessJs(unittest.TestCase):
    def test_js_matches_python_byte_for_byte(self):
        out = subprocess.check_output([NODE, "-e", NODE_SCRIPT], cwd=REPO_ROOT, text=True)
        got = json.loads(out)
        want = [
            {"seed": R.seed_for(h).hex(), "coins": "".join(R.coin_flips(h, n)),
             "dice": ",".join(map(str, R.dice_rolls(h, m)))}
            for (h, n, m) in [(GENESIS, 24, 16), (HASH2, 33, 25)]
        ]
        self.assertEqual(got, want)

    def test_js_rejects_bad_sides_like_python(self):
        script = (
            "const R=require('./static/randomness.js');"
            "const t=s=>{try{R.diceRolls('%s',5,s);return false}catch(e){return true}};"
            "console.log(JSON.stringify([0,1,257,-3].map(t)));" % GENESIS
        )
        out = subprocess.check_output([NODE, "-e", script], cwd=REPO_ROOT, text=True)
        self.assertEqual(json.loads(out), [True, True, True, True])  # JS throws on each, mirroring Python


if __name__ == "__main__":
    unittest.main()
