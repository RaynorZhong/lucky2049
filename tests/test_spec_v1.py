#!/usr/bin/env python3
"""
Regression tests that LOCK the frozen draw algorithm v1 (see SPEC.md).

These tests are the machine-enforced counterpart to the "FROZEN" promise in
SPEC.md: if anyone changes the generation logic or game parameters, these
golden vectors break in CI.

Standard library only — `python -m unittest discover -s tests` needs no
third-party packages and no database/network. verify.py is the single Python
implementation of the algorithm; the in-browser JS copy is locked separately by
tests/test_verify_js.py.
"""
import hashlib
import hmac
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

import verify  # noqa: E402  (stdlib-only module at repo root)

# ---- Frozen parameters (must match SPEC.md / verify.py) ----
NUM_BLOCKCHAIN = 144
BLUE_BALL_MAX, BLUE_BALL_NUM = 35, 5
RED_BALL_MAX, RED_BALL_NUM = 12, 2

# ---- Golden test vectors from SPEC.md section 6 ----
GENESIS_HASH = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
VECTORS = [
    {"draw_id": 0,    "seed": "cbc014f38f94d72431f9e1d2f978ff3db74a0be3ffa0e8fcfc1af92818ea324c",
     "front": [11, 14, 19, 30, 35], "back": [2, 11]},
    {"draw_id": 1,    "seed": "a6fa3f9ae093938dd22eed0d25215a6b97bfaade8aeda3e1eef7038817279746",
     "front": [8, 10, 11, 18, 23],  "back": [6, 11]},
    {"draw_id": 6315, "seed": "0c794f8b23f2dd36f702c9a3fe39d240a38ad04a8af3023fae706301fdba16a6",
     "front": [1, 22, 25, 30, 35],  "back": [4, 11]},
]


def _numbers_from_seed(seed_bytes):
    """Independent re-implementation of SPEC.md Steps 3-5 (seed -> front, back).

    Deliberately NOT importing the production helpers, so this is a genuine
    second opinion that the published numbers follow the spec.
    """
    nums = [
        int.from_bytes(hmac.new(seed_bytes, str(k).encode("ascii"), hashlib.sha256).digest(), "big")
        for k in range(BLUE_BALL_NUM + RED_BALL_NUM)
    ]
    front_pool = list(range(1, BLUE_BALL_MAX + 1))
    front = sorted(front_pool.pop(nums[i] % len(front_pool)) for i in range(BLUE_BALL_NUM))
    back_pool = list(range(1, RED_BALL_MAX + 1))
    back = sorted(back_pool.pop(nums[BLUE_BALL_NUM + i] % len(back_pool)) for i in range(RED_BALL_NUM))
    return front, back


class TestFrozenParameters(unittest.TestCase):
    def test_parameters_match_spec(self):
        self.assertEqual(verify.NUM_BLOCKCHAIN, NUM_BLOCKCHAIN)
        self.assertEqual((verify.BLUE_BALL_MAX, verify.BLUE_BALL_NUM), (BLUE_BALL_MAX, BLUE_BALL_NUM))
        self.assertEqual((verify.RED_BALL_MAX, verify.RED_BALL_NUM), (RED_BALL_MAX, RED_BALL_NUM))
        self.assertEqual(verify.ALGO_VERSION, "v1")

    def test_heights_are_genesis_anchored(self):
        for n in (0, 1, 6315, 12345):
            self.assertEqual(verify.heights_for(n), (n * 144, n * 144 + 143))


class TestSeedToNumbersVectors(unittest.TestCase):
    """Lock SPEC Steps 3-5 against the published seeds (no hashes/DB needed)."""

    def test_all_vectors(self):
        for v in VECTORS:
            front, back = _numbers_from_seed(bytes.fromhex(v["seed"]))
            self.assertEqual(front, v["front"], f"draw {v['draw_id']} front")
            self.assertEqual(back, v["back"], f"draw {v['draw_id']} back")


class TestFullPipelineDraw0(unittest.TestCase):
    """End-to-end lock for the whole pipeline (hashes -> seed -> result) using a
    committed fixture of draw 0's 144 real block hashes. Runs fully offline."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "draw0_hashes.json")) as f:
            cls.fixture = json.load(f)

    def test_fixture_shape(self):
        self.assertEqual(len(self.fixture["hashes"]), NUM_BLOCKCHAIN)
        self.assertEqual(self.fixture["hashes"][0], GENESIS_HASH)

    def test_generate_matches_spec_vector(self):
        front, back, seed_hex = verify.generate(self.fixture["hashes"])
        self.assertEqual(seed_hex, VECTORS[0]["seed"])
        self.assertEqual(front, VECTORS[0]["front"])
        self.assertEqual(back, VECTORS[0]["back"])


if __name__ == "__main__":
    unittest.main()
