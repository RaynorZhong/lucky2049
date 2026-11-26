function chartSection(title, sec, kind) {
    var maxBall = sec.bars.length;
    var tallest = Math.max.apply(null, sec.bars.map(function (b) { return b.count; })) || 1;
    var bars = sec.bars.map(function (b) {
        return '<div class="bar' + (kind === 'back' ? ' back' : '') + '" style="height:' + b.pct + '%;" title="#' + b.n + ': drawn ' + b.count + ' times"></div>';
    }).join("");
    // x-axis: a number under the 1st, last, and every step-th ball
    var step = maxBall > 15 ? 5 : 2;
    var xlabels = sec.bars.map(function (b, i) {
        return '<span>' + (i === 0 || i === maxBall - 1 || b.n % step === 0 ? b.n : '') + '</span>';
    }).join("");
    // A frequency test can detect a deviation, not establish fairness.
    // Keep the decision on the unrounded p-value and format only its display.
    var p = sec.stats.p_value;
    var plain = p === null ? "More draws needed for the approximate frequency test; the observed counts are shown below."
        : p > 0.05
            ? "No statistically significant frequency deviation detected. This does not establish fairness."
            : "Worth a closer look — the number frequencies deviate from the uniform-draw expectation.";
    var pColor = p === null ? 'var(--muted)' : p > 0.05 ? 'var(--accent)' : '#dc2626';
    var pLabel = p === null ? "Not available" : p === 0 ? "< 1e-308"
        : p < 0.0001 ? p.toExponential(2) : p.toFixed(4);
    return '<section class="panel">'
        + '<h2>' + title + '</h2>'
        + '<p class="plain-read">' + plain + '</p>'
        + '<div class="stat-cards">'
        + '<div class="card"><div class="k">Chi-square statistic</div><div class="v">' + sec.stats.chi2 + '</div></div>'
        + '<div class="card"><div class="k">Approximate p-value</div><div class="v" style="color:' + pColor + ';">' + pLabel + '</div></div>'
        + '</div>'
        + '<div class="chart-row">'
        +   '<div class="y-axis">'
        +     (tallest > sec.expected * 1.08 ? '<span class="y-max">' + tallest + '</span>' : '')
        +     '<span class="y-exp" style="bottom:' + sec.expected_pct + '%;">' + sec.expected + '</span>'
        +     '<span class="y-zero">0</span>'
        +   '</div>'
        +   '<div class="chart">'
        +     '<div class="expected" style="bottom:' + sec.expected_pct + '%;"><span class="exp-label">expected ~' + sec.expected + '</span></div>'
        +     '<div class="bars">' + bars + '</div>'
        +   '</div>'
        + '</div>'
        + '<div class="x-axis">' + xlabels + '</div>'
        + '<p class="caption">Each bar is how often a number came up; the gray dashed line is the <strong>expected</strong> count if every number were equally likely (~'
        + sec.expected + '). This tests number frequencies, not every property of randomness.</p>'
        + '</section>';
}
(async function () {
    var idx, sec = document.getElementById("sections");
    try { idx = await (await fetch("./index.json")).json(); }
    catch (e) {
        document.getElementById("intro").textContent = "Could not load draw data.";
        sec.innerHTML = '<div class="placeholder">Couldn’t load draw data — please refresh.</div>';
        return;
    }
    var ov = window.Lucky2049Stats.computeStats(idx.draws || []);
    if (!ov) {
        document.getElementById("intro").textContent = "No draws yet.";
        sec.innerHTML = '<div class="placeholder">No draws to chart yet — statistics appear once the first draw is published.</div>';
        return;
    }
    document.getElementById("intro").textContent =
        "Distribution analysis over " + ov.draws + " draws, recomputed in your browser from the published "
        + "snapshot — compare observed frequencies with the uniform-draw expectation.";
    document.getElementById("sections").innerHTML =
        chartSection("Front Area (1–35)", ov.front, 'front') + chartSection("Back Area (1–12)", ov.back, 'back');
})();
