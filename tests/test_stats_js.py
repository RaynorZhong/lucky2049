#!/usr/bin/env python3
"""
Guards static/stats.js by running it under Node and checking its chi-square
uniformity stats against (a) frozen golden vectors and (b) an independent
pure-Python reference of the same closed form.

The golden (chi2, p) values were computed once with scipy's
chi2_contingency([observed, expected], correction=False) during development, so
both the JS implementation and the Python reference stay pinned to the
authoritative result -- without keeping a scipy dependency in the repo. The
closed form is chi2 = sum_j (O_j - E)^2 / (O_j + E), dof = k - 1, with
p = chi-square survival (regularized upper incomplete gamma).

Stdlib + Node only. Skipped when Node isn't available (Node ships on GitHub's
ubuntu runners, so this still runs in CI).
"""
import json
import math
import os
import random
import shutil
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import verify  # noqa: E402  (frozen game constants, stdlib-only)

NODE = shutil.which("node")
BLUE_MAX, BLUE_NUM = verify.BLUE_BALL_MAX, verify.BLUE_BALL_NUM
RED_MAX, RED_NUM = verify.RED_BALL_MAX, verify.RED_BALL_NUM

# Golden (chi2, p) from scipy chi2_contingency([observed, expected]) — see docstring.
GOLDEN = [
    {"seed": 42, "count": 80,  "front": (13.14, 0.9995), "back": (12.2, 0.3486)},
    {"seed": 7,  "count": 120, "front": (16.44, 0.9952), "back": (3.37, 0.9848)},
]


def _synthetic_draws(count, seed):
    """Deterministic, varied draws (fixed-seed sampling)."""
    rng = random.Random(seed)
    return [{"front": sorted(rng.sample(range(1, BLUE_MAX + 1), BLUE_NUM)),
             "back": sorted(rng.sample(range(1, RED_MAX + 1), RED_NUM))} for _ in range(count)]


def _gammaq(a, x):
    """Regularized upper incomplete gamma Q(a,x) -- mirrors stats.js (Numerical Recipes)."""
    if x <= 0:
        return 1.0
    if x < a + 1:  # series for P, return 1 - P
        ap, total, term = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    fpmin = 1e-300  # continued fraction for Q
    b = x + 1 - a
    c = 1.0 / fpmin
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < fpmin:
            d = fpmin
        c = b + an / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-16:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _chi2_uniform(counts, expected):
    """Independent reference: chi2 = sum (O-E)^2/(O+E), p = chi-square survival."""
    chi2 = sum((o - expected) ** 2 / (o + expected) for o in counts)
    df = len(counts) - 1
    p = 1.0 if chi2 <= 0 else _gammaq(df / 2.0, chi2 / 2.0)
    return round(chi2, 2), round(p, 4)


def _counts(all_nums, max_ball):
    c = [0] * (max_ball + 1)
    for n in all_nums:
        c[n] += 1
    return c[1:]


@unittest.skipUnless(NODE, "node not available")
class TestStatsJs(unittest.TestCase):
    def _run_js(self, draws):
        script = ("const S=require('./static/stats.js');"
                  "const d=" + json.dumps(draws) + ";"
                  "process.stdout.write(JSON.stringify(S.computeStats(d)));")
        return json.loads(subprocess.check_output([NODE, "-e", script], cwd=REPO_ROOT, text=True))

    def test_js_and_reference_match_golden(self):
        for g in GOLDEN:
            draws = _synthetic_draws(g["count"], g["seed"])
            n = len(draws)
            js = self._run_js(draws)
            self.assertEqual(js["draws"], n)
            front_all = [x for d in draws for x in d["front"]]
            back_all = [x for d in draws for x in d["back"]]
            ref = {
                "front": _chi2_uniform(_counts(front_all, BLUE_MAX), n * BLUE_NUM / BLUE_MAX),
                "back": _chi2_uniform(_counts(back_all, RED_MAX), n * RED_NUM / RED_MAX),
            }
            for area in ("front", "back"):
                gold, jstat = g[area], js[area]["stats"]
                msg = f"seed={g['seed']} {area}"
                # JS matches the scipy-derived golden...
                self.assertAlmostEqual(jstat["chi2"], gold[0], delta=0.02, msg=msg + " js chi2")
                self.assertAlmostEqual(jstat["p_value"], gold[1], delta=5e-4, msg=msg + " js p")
                # ...and so does the independent pure-Python reference.
                self.assertAlmostEqual(ref[area][0], gold[0], delta=0.02, msg=msg + " ref chi2")
                self.assertAlmostEqual(ref[area][1], gold[1], delta=5e-4, msg=msg + " ref p")

    def test_bar_counts_and_lengths(self):
        draws = _synthetic_draws(50, 1)
        js = self._run_js(draws)
        self.assertEqual(len(js["front"]["bars"]), BLUE_MAX)
        self.assertEqual(len(js["back"]["bars"]), RED_MAX)
        self.assertEqual(sum(b["count"] for b in js["front"]["bars"]), 50 * BLUE_NUM)
        self.assertEqual(sum(b["count"] for b in js["back"]["bars"]), 50 * RED_NUM)

    def test_empty_draws_returns_null(self):
        out = subprocess.check_output(
            [NODE, "-e", "const S=require('./static/stats.js');process.stdout.write(JSON.stringify(S.computeStats([])))"],
            cwd=REPO_ROOT, text=True)
        self.assertIsNone(json.loads(out))


if __name__ == "__main__":
    unittest.main()
