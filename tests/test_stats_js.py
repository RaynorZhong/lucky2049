#!/usr/bin/env python3
"""
Checks the frequency test under the actual uniform m-of-K draw model. Besides
numerical baselines, fixed-seed null simulations check calibration and a biased
sample catches the former two-row contingency-test error. The survival-function
reference uses finite sums / erfc recurrence rather than the JS gamma algorithm.

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

# Statistical baselines for covariance-corrected m-of-K sampling. These are not
# frozen draw-algorithm vectors; changing the test model must revalidate them.
GOLDEN = [
    {"seed": 42, "count": 80,  "front": (32.385, 0.5468703740), "back": (22.66, 0.0197307183)},
    {"seed": 7,  "count": 120, "front": (34.5288888889, 0.4424894683), "back": (7.26, 0.7776542289)},
]


def _synthetic_draws(count, seed):
    """Deterministic, varied draws (fixed-seed sampling)."""
    rng = random.Random(seed)
    return [{"front": sorted(rng.sample(range(1, BLUE_MAX + 1), BLUE_NUM)),
             "back": sorted(rng.sample(range(1, RED_MAX + 1), RED_NUM))} for _ in range(count)]


def _chi_square_sf(x, df):
    """Independent integer-df reference via Q(a+1,z)=Q(a,z)+z^a*exp(-z)/Γ(a+1)."""
    if x <= 0:
        return 1.0
    z = x / 2
    a = 0.5 if df % 2 else 1.0
    p = math.erfc(math.sqrt(z)) if df % 2 else math.exp(-z)
    while a < df / 2:
        p += math.exp(a * math.log(z) - z - math.lgamma(a + 1))
        a += 1
    return p


def _chi2_uniform(counts, expected, picks):
    """Covariance on the sum-zero subspace is E*(K-m)/(K-1)."""
    df = len(counts) - 1
    variance = expected * (len(counts) - picks) / df
    chi2 = sum((o - expected) ** 2 for o in counts) / variance
    return chi2, _chi_square_sf(chi2, df)


def _counts(all_nums, max_ball):
    c = [0] * (max_ball + 1)
    for n in all_nums:
        c[n] += 1
    return c[1:]


@unittest.skipUnless(NODE, "node not available")
class TestStatsJs(unittest.TestCase):
    def _run_js(self, draws, many=False):
        result = "d.map(x=>S.computeStats(x))" if many else "S.computeStats(d)"
        script = ("const S=require('./static/stats.js'),fs=require('fs');"
                  "const d=JSON.parse(fs.readFileSync(0,'utf8'));"
                  "process.stdout.write(JSON.stringify(" + result + "));")
        return json.loads(subprocess.check_output(
            [NODE, "-e", script], input=json.dumps(draws), cwd=REPO_ROOT, text=True))

    def test_js_and_reference_match_golden(self):
        for g in GOLDEN:
            draws = _synthetic_draws(g["count"], g["seed"])
            n = len(draws)
            js = self._run_js(draws)
            self.assertEqual(js["draws"], n)
            front_all = [x for d in draws for x in d["front"]]
            back_all = [x for d in draws for x in d["back"]]
            ref = {
                "front": _chi2_uniform(_counts(front_all, BLUE_MAX), n * BLUE_NUM / BLUE_MAX, BLUE_NUM),
                "back": _chi2_uniform(_counts(back_all, RED_MAX), n * RED_NUM / RED_MAX, RED_NUM),
            }
            for area in ("front", "back"):
                gold, jstat = g[area], js[area]["stats"]
                msg = f"seed={g['seed']} {area}"
                # The numerical baseline and independent survival function agree.
                self.assertAlmostEqual(jstat["chi2"], gold[0], delta=0.02, msg=msg + " js chi2")
                self.assertAlmostEqual(jstat["p_value"], gold[1], delta=1e-9, msg=msg + " js p")
                self.assertAlmostEqual(ref[area][0], gold[0], delta=0.02, msg=msg + " ref chi2")
                self.assertAlmostEqual(ref[area][1], gold[1], delta=1e-9, msg=msg + " ref p")

    def test_detects_bias_hidden_by_the_old_contingency_model(self):
        rng = random.Random(604)
        draws = []
        for _ in range(1000):
            front = rng.sample(range(1, BLUE_MAX + 1), BLUE_NUM)
            if rng.random() < 0.07 and 1 not in front:
                front[rng.randrange(BLUE_NUM)] = 1
            draws.append({"front": sorted(front),
                          "back": sorted(rng.sample(range(1, RED_MAX + 1), RED_NUM))})
        js = self._run_js(draws)
        self.assertEqual(js["front"]["bars"][0]["count"], 223)
        self.assertAlmostEqual(js["front"]["stats"]["chi2"], 99.3593333333, delta=0.01)
        p = js["front"]["stats"]["p_value"]
        self.assertGreater(p, 0, "keep the p-value rather than rounding small probabilities to zero")
        self.assertAlmostEqual(p, 2.5457864408e-8, delta=1e-15)

    def test_uniform_draws_have_a_calibrated_rejection_rate(self):
        # Actual sampling, not a second implementation of the statistic: the old
        # test rejects none of these 500 uniform samples instead of about 5%.
        samples = [_synthetic_draws(200, seed) for seed in range(500)]
        results = self._run_js(samples, many=True)
        for area in ("front", "back"):
            rejected = sum(r[area]["stats"]["p_value"] <= 0.05 for r in results)
            self.assertGreater(rejected, 10, area)
            self.assertLess(rejected, 45, area)

    def test_small_samples_keep_bars_but_do_not_claim_a_p_value(self):
        one = self._run_js(_synthetic_draws(1, 2))
        for area in ("front", "back"):
            self.assertIsNone(one[area]["stats"]["p_value"])
            self.assertIn("More draws", one[area]["stats"]["conclusion"])
        boundary = self._run_js(_synthetic_draws(34, 2))
        self.assertIsNone(boundary["front"]["stats"]["p_value"])
        self.assertIsNotNone(boundary["back"]["stats"]["p_value"])
        enough = self._run_js(_synthetic_draws(35, 2))
        self.assertIsNotNone(enough["front"]["stats"]["p_value"])

    def test_page_renders_small_samples_and_honest_frequency_conclusions(self):
        script = r"""
const fs=require('fs'),S=require('./static/stats.js');
const html=fs.readFileSync('./web/stats.html','utf8');
const block=html.slice(html.indexOf('function chartSection'),html.indexOf('(async function ()'));
const chart=new Function(block+';return chartSection;')();
const sec=S.computeStats([{front:[1,2,3,4,5],back:[1,2]}]).front;
const render=p=>{const s=JSON.parse(JSON.stringify(sec));s.stats.p_value=p;return chart('Front',s,'front');};
console.log(JSON.stringify({small:render(null),ordinary:render(0.6),biased:render(2.5457864408e-8),
  boundary:render(0.05),aboveBoundary:render(0.05001),belowBoundary:render(0.04999)}));
"""
        rendered = json.loads(subprocess.check_output([NODE, "-e", script], cwd=REPO_ROOT, text=True))
        self.assertIn("More draws", rendered["small"])
        self.assertIn("Not available", rendered["small"])
        self.assertIn("No statistically significant frequency deviation", rendered["ordinary"])
        self.assertNotIn("Looks fair", rendered["ordinary"])
        self.assertIn("Worth a closer look", rendered["biased"])
        self.assertIn("2.55e-8", rendered["biased"])
        self.assertIn("Worth a closer look", rendered["boundary"])
        self.assertIn("No statistically significant frequency deviation", rendered["aboveBoundary"])
        self.assertIn("Worth a closer look", rendered["belowBoundary"])

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
