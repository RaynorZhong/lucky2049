#!/usr/bin/env python3
"""
Guards static/trend.js by running it under Node and checking its trend data
(full-history miss gaps, frequency, max/current gap, the 3+ run flags and empty
zones) against an independent pure-Python reference. The math is plain counting,
so the reference is exact -- no extra deps. Stdlib + Node only; skipped w/o Node.
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


def _area_ref(draws, max_ball, key, window=None):
    """Reference: gaps run over ALL draws; only the last `window` rows are kept."""
    n = len(draws)
    start = max(0, n - window) if window else 0
    gap = [0] * (max_ball + 1)
    freq = [0] * (max_ball + 1)
    max_gap = [0] * (max_ball + 1)
    rows = []
    for i, d in enumerate(draws):
        drawn = set(d[key])
        shown = i >= start
        cells = [] if shown else None
        for b in range(1, max_ball + 1):
            if b in drawn:
                gap[b] = 0
                if shown:
                    freq[b] += 1
                    cells.append((True, 0))
            else:
                gap[b] += 1
                if shown:
                    max_gap[b] = max(max_gap[b], gap[b])
                    cells.append((False, gap[b]))
        if shown:
            rows.append(cells)
    return {"freq": freq[1:], "curGap": gap[1:], "maxGap": max_gap[1:], "rows": rows}


@unittest.skipUnless(NODE, "node not available")
class TestTrendJs(unittest.TestCase):
    def _run_js(self, draws, params=None):
        script = ("const T=require('./static/trend.js');"
                  "const d=" + json.dumps(draws) + ";"
                  "process.stdout.write(JSON.stringify(T.computeTrend(d, " + json.dumps(params or {}) + ")));")
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

    def test_full_history_gap(self):
        draws = _synthetic_draws(80, 11)
        js = self._run_js(draws, {"window": 20})
        self.assertEqual(len(js["front"]["rows"]), 20)
        for key, jarea, mx in (("front", js["front"], BLUE_MAX), ("back", js["back"], RED_MAX)):
            ref = _area_ref(draws, mx, key, window=20)
            self.assertEqual([[(c["drawn"], c["gap"]) for c in row["cells"]] for row in jarea["rows"]],
                             ref["rows"], f"{key} windowed cells (full-history gaps)")
            self.assertEqual(jarea["curGap"], ref["curGap"], f"{key} curGap")
            self.assertEqual(jarea["freq"], ref["freq"], f"{key} freq within window")
        # gaps must reflect ALL history, not just the 20 shown rows
        window_only = _area_ref(draws[-20:], BLUE_MAX, "front")
        full_top = [c["gap"] for c in js["front"]["rows"][0]["cells"]]
        wo_top = [g for (_d, g) in window_only["rows"][0]]
        self.assertNotEqual(full_top, wo_top)

    def test_runs(self):
        draws = [
            {"id": 1, "front": [7, 17, 18, 19, 30], "back": [1, 2]},   # 17,18,19 horizontal run; 7 starts a vertical run
            {"id": 2, "front": [4, 5, 7, 22, 33], "back": [5, 8]},     # 4,5 only a pair (not flagged)
            {"id": 3, "front": [7, 10, 15, 28, 31], "back": [9, 11]},  # 7 drawn 3 draws in a row -> vertical run
        ]
        js = self._run_js(draws)
        r0 = js["front"]["rows"][0]["cells"]
        self.assertTrue(r0[16].get("hrun") and r0[17].get("hrun") and r0[18].get("hrun"))  # 17,18,19
        self.assertFalse(r0[29].get("hrun"))   # 30 isolated
        self.assertFalse(r0[6].get("hrun"))    # 7 isolated horizontally
        for i in range(3):
            self.assertTrue(js["front"]["rows"][i]["cells"][6].get("vrun"), f"7 vrun row {i}")
        self.assertFalse(js["front"]["rows"][1]["cells"][3].get("hrun"))  # 4,5 only a pair
        self.assertFalse(js["back"]["rows"][0]["cells"][0].get("hrun"))   # back 1,2 only a pair

    def test_empty_zones(self):
        js = self._run_js([
            {"id": 1, "front": [1, 2, 3, 4, 5], "back": [1, 2]},        # all in zone 0
            {"id": 2, "front": [5, 15, 16, 25, 30], "back": [3, 4]},    # 1 / 2 / 2 -> none empty
            {"id": 3, "front": [13, 14, 15, 16, 17], "back": [1, 2]},   # all in zone 1
        ])
        self.assertEqual(js["front"]["rows"][0]["emptyZones"], [1, 2])
        self.assertEqual(js["front"]["rows"][1]["emptyZones"], [])
        self.assertEqual(js["front"]["rows"][2]["emptyZones"], [0, 2])

    def test_empty(self):
        js = self._run_js([])
        self.assertEqual(js["count"], 0)
        self.assertEqual(js["front"]["rows"], [])


if __name__ == "__main__":
    unittest.main()
