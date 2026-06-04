#!/usr/bin/env python3
"""
Guards static/trend.js by running it under Node and checking its trend data
(遗漏 / frequency / max-gap / current-gap, and the rank-connected lines) against
an independent pure-Python reference. The trend math is plain counting, so the
reference is exact -- no extra deps. Stdlib + Node only; skipped without Node.
"""
import json
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


def _synthetic_draws(count, seed):
    rng = random.Random(seed)
    return [{"id": 1000 + i,
             "front": sorted(rng.sample(range(1, BLUE_MAX + 1), BLUE_NUM)),
             "back": sorted(rng.sample(range(1, RED_MAX + 1), RED_NUM))}
            for i in range(count)]


def _area_ref(draws, max_ball, key):
    """Pure-Python reference for one area: freq / curGap / maxGap + gap cells."""
    gap = [0] * (max_ball + 1)
    freq = [0] * (max_ball + 1)
    max_gap = [0] * (max_ball + 1)
    rows = []
    for d in draws:
        drawn = set(d[key])
        cells = []
        for b in range(1, max_ball + 1):
            if b in drawn:
                freq[b] += 1
                gap[b] = 0
                cells.append((True, 0))
            else:
                gap[b] += 1
                max_gap[b] = max(max_gap[b], gap[b])
                cells.append((False, gap[b]))
        rows.append(cells)
    return {"freq": freq[1:], "curGap": gap[1:], "maxGap": max_gap[1:], "rows": rows}


@unittest.skipUnless(NODE, "node not available")
class TestTrendJs(unittest.TestCase):
    def _run_js(self, draws):
        script = ("const T=require('./static/trend.js');"
                  "const d=" + json.dumps(draws) + ";"
                  "process.stdout.write(JSON.stringify(T.computeTrend(d)));")
        return json.loads(subprocess.check_output([NODE, "-e", script], cwd=REPO_ROOT, text=True))

    def test_matches_reference(self):
        for seed, count in [(42, 30), (7, 60)]:
            draws = _synthetic_draws(count, seed)
            js = self._run_js(draws)
            self.assertEqual(js["count"], count)
            for key, jarea, mx in (("front", js["front"], BLUE_MAX), ("back", js["back"], RED_MAX)):
                ref = _area_ref(draws, mx, key)
                self.assertEqual(jarea["freq"], ref["freq"], f"seed={seed} {key} freq")
                self.assertEqual(jarea["curGap"], ref["curGap"], f"seed={seed} {key} curGap")
                self.assertEqual(jarea["maxGap"], ref["maxGap"], f"seed={seed} {key} maxGap")
                last_js = [(c["drawn"], c["gap"]) for c in jarea["rows"][-1]["cells"]]
                self.assertEqual(last_js, ref["rows"][-1], f"seed={seed} {key} last-row cells")
            self.assertEqual(sum(js["front"]["freq"]), count * BLUE_NUM)
            self.assertEqual(sum(js["back"]["freq"]), count * RED_NUM)

    def test_front_row_stats(self):
        zones = [(1, 12), (13, 24), (25, 35)]
        draws = _synthetic_draws(40, 9)
        js = self._run_js(draws)
        for i, d in enumerate(draws):
            nums = sorted(d["front"])
            row = js["front"]["rows"][i]
            self.assertEqual(row["sum"], sum(nums), f"row {i} sum")
            self.assertEqual(row["span"], nums[-1] - nums[0], f"row {i} span")
            self.assertEqual(row["zoneRatio"],
                             [sum(1 for x in nums if a <= x <= b) for a, b in zones], f"row {i} zones")
            self.assertEqual([row["odd"], row["even"]],
                             [sum(x % 2 for x in nums), sum(1 - x % 2 for x in nums)], f"row {i} odd/even")
        # zone ratio always sums to the number of front picks
        self.assertTrue(all(sum(r["zoneRatio"]) == BLUE_NUM for r in js["front"]["rows"]))

    def test_empty(self):
        js = self._run_js([])
        self.assertEqual(js["count"], 0)
        self.assertEqual(js["front"]["rows"], [])


if __name__ == "__main__":
    unittest.main()
