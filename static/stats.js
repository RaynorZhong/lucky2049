/*
 * stats.js -- dependency-free distribution stats for the static lucky2049 site.
 *
 * Recomputes, in the browser, per-number frequencies + a chi-square uniformity
 * test. For the 2xk observed/expected table the chi-square reduces to the closed
 * form
 *     chi2 = sum_j (O_j - E)^2 / (O_j + E),   dof = k - 1,
 *     p    = P(X > chi2),  X ~ chi-square(dof)
 * pinned to frozen golden (chi2, p) values (computed once with scipy) plus an
 * independent pure-Python reference in tests/test_stats_js.py -- so it stays
 * authoritative without any scipy dependency in the repo.
 *
 * Pure computation only (no DOM), so it runs in the browser and under Node.
 */
(function (root) {
  "use strict";

  // ---- log-gamma (Lanczos g=7) ----
  function gammaln(x) {
    var c = [
      0.99999999999980993, 676.5203681218851, -1259.1392167224028,
      771.32342877765313, -176.61502916214059, 12.507343278686905,
      -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7
    ];
    if (x < 0.5) {
      return Math.log(Math.PI / Math.sin(Math.PI * x)) - gammaln(1 - x);
    }
    x -= 1;
    var a = c[0];
    var t = x + 7.5;
    for (var i = 1; i < 9; i++) a += c[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
  }

  // lower regularized P(a,x) via series (Numerical Recipes gser; for x < a+1)
  function gserP(a, x) {
    if (x <= 0) return 0;
    var ap = a, sum = 1 / a, del = sum;
    for (var n = 0; n < 1000; n++) {
      ap += 1;
      del *= x / ap;
      sum += del;
      if (Math.abs(del) < Math.abs(sum) * 1e-16) break;
    }
    return sum * Math.exp(-x + a * Math.log(x) - gammaln(a));
  }

  // upper regularized Q(a,x) via continued fraction (NR gcf; for x >= a+1)
  function gcfQ(a, x) {
    var FPMIN = 1e-300;
    var b = x + 1 - a, c = 1 / FPMIN, d = 1 / b, h = d;
    for (var i = 1; i < 1000; i++) {
      var an = -i * (i - a);
      b += 2;
      d = an * d + b; if (Math.abs(d) < FPMIN) d = FPMIN;
      c = b + an / c; if (Math.abs(c) < FPMIN) c = FPMIN;
      d = 1 / d;
      var del = d * c;
      h *= del;
      if (Math.abs(del - 1) < 1e-16) break;
    }
    return Math.exp(-x + a * Math.log(x) - gammaln(a)) * h;
  }

  // regularized upper incomplete gamma Q(a,x) = 1 - P(a,x)
  function gammaQ(a, x) {
    if (x < 0 || a <= 0) return NaN;
    if (x === 0) return 1;
    return x < a + 1 ? 1 - gserP(a, x) : gcfQ(a, x);
  }

  // chi-square survival: P(X > x) for X ~ chi-square with k degrees of freedom
  function chiSquareSf(x, k) {
    if (x <= 0) return 1;
    return gammaQ(k / 2, x / 2);
  }

  function _round(x, dp) {
    var f = Math.pow(10, dp);
    return Math.round(x * f) / f;
  }

  // One area (front or back): frequency bars + chi-square uniformity test.
  function section(allNums, maxBall, picks, n) {
    var counts = new Array(maxBall + 1).fill(0);
    for (var i = 0; i < allNums.length; i++) counts[allNums[i]]++;
    var freq = [];
    for (var b = 1; b <= maxBall; b++) freq.push(counts[b] || 0);
    var tallest = Math.max.apply(null, freq) || 1;
    var expected = n * picks / maxBall;

    var chi2 = 0;
    for (var j = 0; j < freq.length; j++) {
      var diff = freq[j] - expected;
      chi2 += diff * diff / (freq[j] + expected);
    }
    var p = chiSquareSf(chi2, maxBall - 1);

    return {
      bars: freq.map(function (c, i) {
        return { n: i + 1, count: c, pct: _round(c / tallest * 100, 2) };
      }),
      expected_pct: _round(expected / tallest * 100, 2),
      expected: _round(expected, 1),
      stats: {
        chi2: _round(chi2, 2),
        p_value: _round(p, 4),
        conclusion: p > 0.05
          ? "No statistically significant deviation (p > 0.05)"
          : "Statistically significant deviation (p < 0.05)"
      }
    };
  }

  // draws: [{front:[...], back:[...]}, ...] (e.g. index.json's `draws`).
  // Returns {draws, front, back} like the server's get_stats_overview(), or
  // null when there are no draws.
  function computeStats(draws, params) {
    params = params || {};
    var blueMax = params.blueMax || 35, blueNum = params.blueNum || 5;
    var redMax = params.redMax || 12, redNum = params.redNum || 2;
    var n = draws.length;
    if (n === 0) return null;
    var frontAll = [], backAll = [];
    for (var i = 0; i < n; i++) {
      var f = draws[i].front || [], bk = draws[i].back || [];
      for (var a = 0; a < f.length; a++) frontAll.push(f[a]);
      for (var c = 0; c < bk.length; c++) backAll.push(bk[c]);
    }
    return {
      draws: n,
      front: section(frontAll, blueMax, blueNum, n),
      back: section(backAll, redMax, redNum, n)
    };
  }

  var api = {
    computeStats: computeStats,
    chiSquareSf: chiSquareSf,
    gammaQ: gammaQ,
    gammaln: gammaln
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Lucky2049Stats = api;
})(typeof window !== "undefined" ? window : globalThis);
