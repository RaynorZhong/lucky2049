#!/usr/bin/env python3
"""
Guards static/stats.js: runs it under Node and checks its chi-square uniformity
stats match the server's scipy implementation (app.lotto.chi_square_test), so
the static /stats page can never drift from what the dynamic /stats page shows.

Skipped unless both Node and scipy are available (Node ships on GitHub's ubuntu
runners; scipy is present in the full-deps CI job).
"""
import json
import os
import random
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

try:
    from app.lotto import (
        chi_square_test, BLUE_BALL_MAX, BLUE_BALL_NUM, RED_BALL_MAX, RED_BALL_NUM,
    )
    HAVE_DEPS = True
except Exception:  # scipy/numpy or app deps missing -> stdlib CI job skips this
    HAVE_DEPS = False
    BLUE_BALL_MAX, BLUE_BALL_NUM, RED_BALL_MAX, RED_BALL_NUM = 35, 5, 12, 2


def _synthetic_draws(count, seed=42):
    """Deterministic, varied draws (fixed-seed sampling) so chi2/p land in a
    meaningful range that actually exercises the survival function."""
    rng = random.Random(seed)
    draws = []
    for _ in range(count):
        front = sorted(rng.sample(range(1, BLUE_BALL_MAX + 1), BLUE_BALL_NUM))
        back = sorted(rng.sample(range(1, RED_BALL_MAX + 1), RED_BALL_NUM))
        draws.append({"front": front, "back": back})
    return draws


@unittest.skipUnless(NODE and HAVE_DEPS, "node or scipy not available")
class TestStatsJsMatchesScipy(unittest.TestCase):
    def _run_js(self, draws):
        script = (
            "const S=require('./static/stats.js');"
            "const d=" + json.dumps(draws) + ";"
            "process.stdout.write(JSON.stringify(S.computeStats(d)));"
        )
        out = subprocess.check_output([NODE, "-e", script], cwd=REPO_ROOT, text=True)
        return json.loads(out)

    def test_chi_square_matches_for_front_and_back(self):
        draws = _synthetic_draws(80)
        js = self._run_js(draws)
        self.assertEqual(js["draws"], len(draws))

        front_all = [x for d in draws for x in d["front"]]
        back_all = [x for d in draws for x in d["back"]]
        f_chi2, f_p = chi_square_test(front_all, BLUE_BALL_MAX, len(draws) * BLUE_BALL_NUM / BLUE_BALL_MAX)
        b_chi2, b_p = chi_square_test(back_all, RED_BALL_MAX, len(draws) * RED_BALL_NUM / RED_BALL_MAX)

        # JS rounds chi2->2dp, p->4dp; small deltas absorb rounding + igamc noise.
        self.assertAlmostEqual(js["front"]["stats"]["chi2"], f_chi2, delta=0.02)
        self.assertAlmostEqual(js["front"]["stats"]["p_value"], f_p, delta=5e-4)
        self.assertAlmostEqual(js["back"]["stats"]["chi2"], b_chi2, delta=0.02)
        self.assertAlmostEqual(js["back"]["stats"]["p_value"], b_p, delta=5e-4)
        # sanity: the random draws should not look biased
        self.assertGreater(js["front"]["stats"]["p_value"], 0.05)

    def test_bar_counts_and_lengths(self):
        draws = _synthetic_draws(50)
        js = self._run_js(draws)
        self.assertEqual(len(js["front"]["bars"]), BLUE_BALL_MAX)
        self.assertEqual(len(js["back"]["bars"]), RED_BALL_MAX)
        # every drawn number is accounted for exactly once
        self.assertEqual(sum(b["count"] for b in js["front"]["bars"]), len(draws) * BLUE_BALL_NUM)
        self.assertEqual(sum(b["count"] for b in js["back"]["bars"]), len(draws) * RED_BALL_NUM)

    def test_empty_draws_returns_null(self):
        out = subprocess.check_output(
            [NODE, "-e", "const S=require('./static/stats.js');process.stdout.write(JSON.stringify(S.computeStats([])))"],
            cwd=REPO_ROOT, text=True,
        )
        self.assertEqual(json.loads(out), None)


if __name__ == "__main__":
    unittest.main()
