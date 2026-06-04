/*
 * trend.js -- dependency-free data for the Sina-style trend chart.
 *
 * For a window of draws it builds, for the front area (1..35) and the back area
 * (1..12): per-draw rows where each number is either drawn (a ball) or shows its
 * running miss gap (consecutive draws since it last appeared, within the window),
 * plus frequency, max gap and current gap per number. For each draw it also
 * derives the front-area summaries shown by Sina-style charts: sum, span,
 * zone ratio (how the 5 front numbers split across the three zones) and the
 * odd:even split.
 *
 * Pure computation only (no DOM), so it runs in the browser and under Node and
 * tests can pin it. The HTML turns this into one unified table.
 */
(function (root) {
  "use strict";

  // Three zones over the 1..35 front area (Sina splits the "red" balls likewise).
  var FRONT_ZONES = [[1, 12], [13, 24], [25, 35]];

  function area(draws, maxBall, picks, key) {
    var n = draws.length;
    var gap = new Array(maxBall + 1).fill(0);     // running miss streak, 1-indexed
    var freq = new Array(maxBall + 1).fill(0);
    var maxGap = new Array(maxBall + 1).fill(0);
    var rows = [];

    for (var i = 0; i < n; i++) {
      var drawn = {};
      var nums = (draws[i][key] || []).slice().sort(function (a, b) { return a - b; });
      for (var j = 0; j < nums.length; j++) drawn[nums[j]] = true;

      var cells = [];
      for (var b = 1; b <= maxBall; b++) {
        if (drawn[b]) {
          freq[b]++; gap[b] = 0;
          cells.push({ drawn: true, gap: 0 });
        } else {
          gap[b]++;
          if (gap[b] > maxGap[b]) maxGap[b] = gap[b];
          cells.push({ drawn: false, gap: gap[b] });
        }
      }
      rows.push({ id: draws[i].id, nums: nums, cells: cells });
    }

    return {
      maxBall: maxBall,
      picks: picks,
      rows: rows,
      freq: freq.slice(1),
      maxGap: maxGap.slice(1),
      curGap: gap.slice(1),     // gap after the last row, per number
    };
  }

  // Per-draw front summaries: sum, span, zone ratio, odd/even (nums sorted asc).
  function frontStats(nums, zones) {
    var sum = 0, odd = 0;
    for (var i = 0; i < nums.length; i++) { sum += nums[i]; if (nums[i] % 2) odd++; }
    var span = nums.length ? nums[nums.length - 1] - nums[0] : 0;
    var zoneRatio = zones.map(function (z) {
      return nums.filter(function (x) { return x >= z[0] && x <= z[1]; }).length;
    });
    return { sum: sum, span: span, zoneRatio: zoneRatio, odd: odd, even: nums.length - odd };
  }

  // draws: [{id, front:[...], back:[...]}, ...] (a window, oldest first).
  function computeTrend(draws, params) {
    params = params || {};
    var blueMax = params.blueMax || 35, blueNum = params.blueNum || 5;
    var redMax = params.redMax || 12, redNum = params.redNum || 2;
    var front = area(draws, blueMax, blueNum, "front");
    var back = area(draws, redMax, redNum, "back");
    for (var i = 0; i < front.rows.length; i++) {
      var s = frontStats(front.rows[i].nums, FRONT_ZONES);
      front.rows[i].sum = s.sum;
      front.rows[i].span = s.span;
      front.rows[i].zoneRatio = s.zoneRatio;
      front.rows[i].odd = s.odd;
      front.rows[i].even = s.even;
    }
    front.zones = FRONT_ZONES;
    return { count: draws.length, front: front, back: back };
  }

  var api = { computeTrend: computeTrend, FRONT_ZONES: FRONT_ZONES };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Lucky2049Trend = api;
})(typeof window !== "undefined" ? window : globalThis);
