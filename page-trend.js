(function () {
    "use strict";
    var DRAWS = null, WINDOW = 30;
    var ZONE_LABELS = ['Zone 1', 'Zone 2', 'Zone 3'];

    function zoneIdx(n, zones) {
        for (var i = 0; i < zones.length; i++) if (n >= zones[i][0] && n <= zones[i][1]) return i;
        return 0;
    }
    function footVal(v, ri, isFront) {
        if (ri === 0) return '<b class="ff">' + v + '</b>';
        if (ri === 2 && v >= (isFront ? 14 : 11)) return '<b class="hot">' + v + '</b>';
        return '<i class="fg">' + v + '</i>';
    }
    function ball(n, isBack, cell) {
        return '<span class="b' + (isBack ? ' bk' : '') + (cell.hrun ? ' hrun' : '') + (cell.vrun ? ' vrun' : '') + '">' + n + '</span>';
    }

    function buildTable(t) {
        var f = t.front, b = t.back, zones = f.zones, n;
        var cg = '<colgroup><col class="ci">';
        for (n = 0; n < f.maxBall + b.maxBall; n++) cg += '<col class="cn">';
        cg += '</colgroup>';

        var h = '<tr><th class="issue" rowspan="2">Issue</th>';
        zones.forEach(function (z, zi) {
            h += '<th colspan="' + (z[1] - z[0] + 1) + '" class="zh z' + (zi % 2) + (zi > 0 ? ' sep' : '') + '">'
                + ZONE_LABELS[zi] + '<span class="zr">' + z[0] + '–' + z[1] + '</span></th>';
        });
        h += '<th colspan="' + b.maxBall + '" class="zh cb sep">Back<span class="zr">1–' + b.maxBall + '</span></th></tr>';
        h += '<tr>';
        for (n = 1; n <= f.maxBall; n++) { var zi = zoneIdx(n, zones); h += '<th class="z' + (zi % 2) + (n === zones[zi][0] && zi > 0 ? ' sep' : '') + '">' + n + '</th>'; }
        for (n = 1; n <= b.maxBall; n++) h += '<th class="cb' + (n === 1 ? ' sep' : '') + '">' + n + '</th>';
        h += '</tr>';

        var body = '';
        for (var i = 0; i < f.rows.length; i++) {
            var fr = f.rows[i], br = b.rows[i], empty = fr.emptyZones || [];
            body += '<tr><td class="issue">' + (fr.id | 0) + '</td>';
            for (n = 1; n <= f.maxBall; n++) {
                var zi2 = zoneIdx(n, zones), c = fr.cells[n - 1];
                var cls = 'z' + (zi2 % 2) + (n === zones[zi2][0] && zi2 > 0 ? ' sep' : '') + (empty.indexOf(zi2) >= 0 ? ' ez' : '');
                body += '<td class="' + cls + '">' + (c.drawn ? ball(n, false, c) : '<i class="g">' + c.gap + '</i>') + '</td>';
            }
            for (n = 1; n <= b.maxBall; n++) {
                var cb = br.cells[n - 1];
                body += '<td class="cb' + (n === 1 ? ' sep' : '') + '">' + (cb.drawn ? ball(n, true, cb) : '<i class="g">' + cb.gap + '</i>') + '</td>';
            }
            body += '</tr>';
        }

        var foot = '';
        [['Freq', f.freq, b.freq], ['Max gap', f.maxGap, b.maxGap], ['Cur gap', f.curGap, b.curGap]].forEach(function (rd, ri) {
            foot += '<tr class="ft"><td class="issue">' + rd[0] + '</td>';
            for (n = 1; n <= f.maxBall; n++) { var zi3 = zoneIdx(n, zones); foot += '<td class="z' + (zi3 % 2) + (n === zones[zi3][0] && zi3 > 0 ? ' sep' : '') + '">' + footVal(rd[1][n - 1], ri, true) + '</td>'; }
            for (n = 1; n <= b.maxBall; n++) foot += '<td class="cb' + (n === 1 ? ' sep' : '') + '">' + footVal(rd[2][n - 1], ri, false) + '</td>';
            foot += '</tr>';
        });

        return '<table class="ttab">' + cg + '<thead>' + h + '</thead><tbody>' + body + '</tbody><tfoot>' + foot + '</tfoot></table>';
    }

    var TREND_CACHE = {};
    function render() {
        if (!DRAWS) return;
        var t = TREND_CACHE[WINDOW] || (TREND_CACHE[WINDOW] = window.Lucky2049Trend.computeTrend(DRAWS, { window: WINDOW }));
        document.getElementById('grid').innerHTML = buildTable(t);
        var rows = t.front.rows;
        if (rows.length) document.getElementById('range').textContent =
            rows.length + ' draws (No. ' + rows[0].id + ' – ' + rows[rows.length - 1].id + ')';
    }

    document.getElementById('seg').addEventListener('click', function (e) {
        var btn = e.target.closest('button'); if (!btn) return;
        WINDOW = parseInt(btn.dataset.n, 10);
        [].forEach.call(this.querySelectorAll('button'), function (x) {
            var on = x === btn; x.classList.toggle('active', on); x.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        render();
    });

    (async function () {
        var idx, grid = document.getElementById("grid");
        try { idx = await (await fetch("./index.json")).json(); }
        catch (e) {
            document.getElementById("intro").textContent = "Could not load draw data.";
            grid.innerHTML = '<div class="placeholder">Couldn’t load draw data — please refresh.</div>';
            return;
        }
        DRAWS = idx.draws || [];
        if (!DRAWS.length) {
            document.getElementById("intro").textContent = "No draws yet.";
            grid.innerHTML = '<div class="placeholder">No draws to chart yet — the trend grid fills in once draws are published.</div>';
            return;
        }
        render();
    })();
})();
