#!/usr/bin/env python3
"""
Golden tests for the tamper-evidence commitment chain (verify.commitment_for).

Locks the commitment formula: if it ever changes, every previously anchored
head becomes unverifiable, so this must be as frozen as the draw algorithm.
Stdlib only — runs in the fast CI job.
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import verify  # noqa: E402

# Golden vectors (derived from SPEC.md draws 0 and 1, chained from GENESIS_PREV).
DRAW0_SEED = "cbc014f38f94d72431f9e1d2f978ff3db74a0be3ffa0e8fcfc1af92818ea324c"
DRAW1_SEED = "a6fa3f9ae093938dd22eed0d25215a6b97bfaade8aeda3e1eef7038817279746"
DRAW0_COMMITMENT = "def4cd38f63acc6f39a9c4dbe0df021c00e219907cbfeb58752be198092b0739"
DRAW1_COMMITMENT = "806f5e794355a757432df4193916c585c7934fda3716e3a7328d34504986951f"


class TestCommitmentGolden(unittest.TestCase):
    def test_genesis_prev_shape(self):
        self.assertEqual(verify.GENESIS_PREV, "0" * 64)

    def test_draw0_commitment(self):
        c0 = verify.commitment_for(
            verify.GENESIS_PREV, 0, "v1", DRAW0_SEED, [11, 14, 19, 30, 35], [2, 11], 0, 143
        )
        self.assertEqual(c0, DRAW0_COMMITMENT)

    def test_draw1_chains_onto_draw0(self):
        c1 = verify.commitment_for(
            DRAW0_COMMITMENT, 1, "v1", DRAW1_SEED, [8, 10, 11, 18, 23], [6, 11], 144, 287
        )
        self.assertEqual(c1, DRAW1_COMMITMENT)

    def test_sensitive_to_every_field(self):
        base = dict(prev_hex=verify.GENESIS_PREV, draw_id=0, algo_version="v1",
                    seed_hex=DRAW0_SEED, front=[11, 14, 19, 30, 35], back=[2, 11],
                    start_height=0, end_height=143)
        ref = verify.commitment_for(**base)
        mutations = [
            {"prev_hex": "1" * 64},
            {"draw_id": 1},
            {"algo_version": "v2"},
            {"seed_hex": DRAW1_SEED},
            {"front": [11, 14, 19, 30, 34]},
            {"back": [2, 12]},
            {"start_height": 1},
            {"end_height": 144},
        ]
        for mut in mutations:
            altered = verify.commitment_for(**{**base, **mut})
            self.assertNotEqual(altered, ref, f"commitment ignored change: {mut}")


class TestLottoUsesSameCommitment(unittest.TestCase):
    def test_lotto_imports_the_same_function(self):
        try:
            import lotto
        except Exception as e:  # pragma: no cover - deps not installed in stdlib CI job
            self.skipTest(f"lotto.py not importable (deps missing): {e}")
        self.assertIs(lotto.commitment_for, verify.commitment_for)
        self.assertEqual(lotto.GENESIS_PREV, verify.GENESIS_PREV)


if __name__ == "__main__":
    unittest.main()
