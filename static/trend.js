/*
 * trend.js -- dependency-free data for the Sina-style trend chart.
 *
 * Given a window of draws (e.g. the last 30 from index.json), it builds, for the
 * front area (1..35) and the back area (1..12):
 *   - per-draw rows with each number's state: drawn (a ball) or its running miss
 *     gap (consecutive draws since it last appeared, within the window),
 *   - frequency, max gap, and current gap per number,
 *   - rank-connected polylines (the trend lines): for each pick rank k, the k-th
 *     smallest drawn number joined across consecutive draws.
 *
 * Pure computation only (no DOM), so it runs in the browser and under Node and
 * tests can pin it. The HTML turns rows/lines into the SVG grid.
 */
(function (root) {
  "use strict";

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

    // trend lines: one polyline per pick rank, joining the k-th smallest drawn
    // number across draws. Coordinates are resolved by the renderer.
    var lines = [];
    for (var k = 0; k < picks; k++) {
      var pts = [];
      for (var r = 0; r < rows.length; r++) {
        if (rows[r].nums.length > k) pts.push({ row: r, num: rows[r].nums[k] });
      }
      lines.push(pts);
    }

    return {
      maxBall: maxBall,
      picks: picks,
      rows: rows,
      freq: freq.slice(1),
      maxGap: maxGap.slice(1),
      curGap: gap.slice(1),     // gap after the last row, per number
      lines: lines,
    };
  }

  // draws: [{id, front:[...], back:[...]}, ...] (a window, oldest first).
  function computeTrend(draws, params) {
    params = params || {};
    var blueMax = params.blueMax || 35, blueNum = params.blueNum || 5;
    var redMax = params.redMax || 12, redNum = params.redNum || 2;
    return {
      count: draws.length,
      front: area(draws, blueMax, blueNum, "front"),
      back: area(draws, redMax, redNum, "back"),
    };
  }

  var api = { computeTrend: computeTrend };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Lucky2049Trend = api;
})(typeof window !== "undefined" ? window : globalThis);
