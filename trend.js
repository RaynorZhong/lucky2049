/*
 * trend.js -- dependency-free data for the trend chart (走势图).
 *
 * Given the FULL list of draws and a display window, it builds, for the front
 * area (1..35) and the back area (1..12):
 *   - per-draw rows (only the last `window` draws are returned) where each
 *     number is drawn (a ball) or shows its miss gap. The gap is the TRUE gap
 *     over all history (it carries across the window boundary).
 *   - frequency (within the window), max gap and current gap per number,
 *   - run flags on drawn cells: hrun (part of a horizontal run of >=3 consecutive
 *     numbers in the same draw) and vrun (part of a vertical run of >=3
 *     consecutive draws of the same number),
 *   - front-area emptyZones per draw (which of the 3 zones had no number).
 *
 * Pure computation only (no DOM); runs in the browser and under Node so tests
 * can pin it.
 */
(function (root) {
  "use strict";

  var FRONT_ZONES = [[1, 12], [13, 24], [25, 35]];

  function area(draws, maxBall, picks, key, window) {
    var n = draws.length;
    var start = (window && window < n) ? n - window : 0;
    var gap = new Array(maxBall + 1).fill(0);     // running TRUE miss streak, over all history
    var freq = new Array(maxBall + 1).fill(0);    // within the shown window
    var maxGap = new Array(maxBall + 1).fill(0);  // max gap visible in the shown window
    var vstreak = new Array(maxBall + 1).fill(0); // consecutive-draw streak per number
    var rows = [];
    var rowByGi = {};                              // global draw index -> shown row

    function markVRun(num, a, b) {                 // mark shown cells of a vertical run [a..b]
      for (var gi = a; gi <= b; gi++) { var row = rowByGi[gi]; if (row) row.cells[num - 1].vrun = true; }
    }

    for (var i = 0; i < n; i++) {
      var nums = (draws[i][key] || []).slice().sort(function (a, b) { return a - b; });
      var drawn = {};
      for (var j = 0; j < nums.length; j++) drawn[nums[j]] = true;

      var shown = i >= start;
      var cells = shown ? [] : null;
      for (var b = 1; b <= maxBall; b++) {
        if (drawn[b]) {
          gap[b] = 0;
          if (shown) { freq[b]++; cells.push({ drawn: true, gap: 0 }); }
        } else {
          gap[b]++;
          if (shown) { if (gap[b] > maxGap[b]) maxGap[b] = gap[b]; cells.push({ drawn: false, gap: gap[b] }); }
        }
      }
      if (shown) { var row = { id: draws[i].id, nums: nums, cells: cells }; rows.push(row); rowByGi[i] = row; }

      // vertical runs: extend streaks, flush when a number's run ends (>=3 marked)
      for (var v = 1; v <= maxBall; v++) {
        if (drawn[v]) { vstreak[v]++; }
        else { if (vstreak[v] >= 3) markVRun(v, i - vstreak[v], i - 1); vstreak[v] = 0; }
      }

      // horizontal runs (>=3 consecutive numbers) within this draw
      if (shown) {
        var p = 0;
        while (p < nums.length) {
          var q = p;
          while (q + 1 < nums.length && nums[q + 1] === nums[q] + 1) q++;
          if (q - p + 1 >= 3) for (var k = p; k <= q; k++) cells[nums[k] - 1].hrun = true;
          p = q + 1;
        }
      }
    }
    for (var t = 1; t <= maxBall; t++) if (vstreak[t] >= 3) markVRun(t, n - vstreak[t], n - 1);

    return {
      maxBall: maxBall, picks: picks, rows: rows,
      freq: freq.slice(1), maxGap: maxGap.slice(1), curGap: gap.slice(1),
    };
  }

  // draws: the FULL [{id, front, back}, ...] (oldest first). params.window limits
  // how many recent draws are returned (gaps and runs still use all history).
  function computeTrend(draws, params) {
    params = params || {};
    var blueMax = params.blueMax || 35, blueNum = params.blueNum || 5;
    var redMax = params.redMax || 12, redNum = params.redNum || 2;
    var win = params.window || 0;
    var front = area(draws, blueMax, blueNum, "front", win);
    var back = area(draws, redMax, redNum, "back", win);
    for (var i = 0; i < front.rows.length; i++) {
      var nums = front.rows[i].nums, empty = [];
      FRONT_ZONES.forEach(function (z, zi) {
        var c = 0;
        for (var x = 0; x < nums.length; x++) if (nums[x] >= z[0] && nums[x] <= z[1]) c++;
        if (c === 0) empty.push(zi);
      });
      front.rows[i].emptyZones = empty;
    }
    front.zones = FRONT_ZONES;
    return { count: draws.length, front: front, back: back };
  }

  var api = { computeTrend: computeTrend, FRONT_ZONES: FRONT_ZONES };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Lucky2049Trend = api;
})(typeof window !== "undefined" ? window : globalThis);
