(function () {
    "use strict";
    var $ = function (id) { return document.getElementById(id); };
    function balls(nums, extra) {
        return (nums || []).map(function (n) { return '<div class="white-balls' + (extra ? ' ' + extra : '') + '">' + (n | 0) + '</div>'; }).join("");
    }
    // Escape any string before it goes into innerHTML (defense-in-depth: the
    // homepage builds the table from same-origin JSON, but never trust strings).
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    // ---- next-draw ETA: 144 blocks/draw, ~10 min/block, 6 confirmations ----
    var etaTarget = null;
    function fmtLeft(ms) {
        if (ms <= 0) return "soon";
        var s = Math.floor(ms / 1000), d = Math.floor(s / 86400);
        s -= d * 86400; var h = Math.floor(s / 3600); s -= h * 3600;
        var m = Math.floor(s / 60); s -= m * 60;
        var pad = function (x) { return (x < 10 ? "0" : "") + x; };
        return (d ? d + "d " : "") + pad(h) + ":" + pad(m) + ":" + pad(s);
    }
    // Absolute time as UTC, matching the draws-list timestamps (no local tz).
    function fmtUtc(d) {
        var p = function (x) { return (x < 10 ? "0" : "") + x; };
        return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate())
            + " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + ":" + p(d.getUTCSeconds()) + " UTC";
    }
    function tick() {
        if (etaTarget == null) return;
        $("ndCd").textContent = fmtLeft(etaTarget - Date.now());
    }
    // Chain tip with fallback (mempool -> blockstream), same sources as verify.html.
    var TIP_APIS = ["https://mempool.space/api/blocks/tip/height", "https://blockstream.info/api/blocks/tip/height"];
    async function fetchTipHeight() {
        for (var i = 0; i < TIP_APIS.length; i++) {
            try {
                var resp = await fetch(TIP_APIS[i]);
                if (!resp.ok) throw new Error("HTTP " + resp.status);
                var h = parseInt((await resp.text()).trim(), 10);
                if (h > 0) return h;
            } catch (e) { /* try the next source */ }
        }
        throw new Error("all explorers failed for the chain tip");
    }
    async function nextDraw(latestId) {
        var nextId = latestId + 1;
        var target = nextId * 144 + 143 + 6;   // last block of the window + 6 confirmations
        $("ndId").textContent = "No. " + nextId;
        var tip;
        try { tip = await fetchTipHeight(); }
        catch (e) { return; }
        var remaining = Math.max(0, target - tip);
        $("ndBlocks").textContent = remaining === 0 ? "0 — ready" : remaining + " blocks";
        if (remaining === 0) {
            etaTarget = null;
            $("ndEta").textContent = "awaiting publish";
            $("ndCd").textContent = "imminent";
            return;
        }
        etaTarget = Date.now() + remaining * 10 * 60 * 1000;
        $("ndEta").textContent = fmtUtc(new Date(etaTarget));
        tick();
    }

    // ---- source health: which hash sources answered on the last refresh ----
    async function sourceHealth() {
        var el = $("srcHealth"), st;
        try { st = await (await fetch("./status.json")).json(); } catch (e) { return; }
        if (!st || !st.sources || !st.sources.length) return;
        el.textContent = "";
        var label = document.createElement("span");
        label.textContent = "Sources (last refresh):";
        el.appendChild(label);
        var oks = st.sources.filter(function (s) { return s.ok; }).map(function (s) { return s.tip; });
        var best = oks.length ? Math.max.apply(null, oks) : 0;
        st.sources.forEach(function (s) {
            var sp = document.createElement("span");
            sp.setAttribute("role", "img");
            var dot = document.createElement("span");
            dot.className = "dot";
            dot.setAttribute("aria-hidden", "true");
            var lagging = s.ok && s.tip < best - 2;
            var state = !s.ok ? "down" : (lagging ? "lagging" : "ok");
            dot.style.background = !s.ok ? "#dc2626" : (lagging ? "#d97706" : "#16a34a");
            sp.appendChild(dot);
            sp.appendChild(document.createTextNode(s.name + (s.ok ? " · " + s.tip + (lagging ? " · lagging" : "") : " · down")));
            sp.title = s.ok ? ("tip " + s.tip + " in " + s.ms + " ms" + (lagging ? " (lagging)" : "")) : (s.error || "unreachable");
            sp.setAttribute("aria-label", s.name + " source " + state + (s.ok ? ", tip " + s.tip : ""));
            el.appendChild(sp);
        });
        var age = Math.round((Date.now() / 1000 - st.checked_at_unix) / 60);
        var when = document.createElement("span");
        when.textContent = "checked " + (age < 1 ? "just now" : age + " min ago");
        if (age > 240) { when.textContent += " — stale"; when.style.color = "#dc2626"; }
        el.appendChild(when);
        el.style.display = "flex";
    }

    function feedDraw(it) {
        if (it._lucky2049) return it._lucky2049;
        var m = /Front ([\d ]+) \| Back ([\d ]+)\s+\(heights (\d+)-(\d+)\)/.exec(it.content_text || "");
        return m
            ? { front: m[1].trim().split(/\s+/).map(Number), back: m[2].trim().split(/\s+/).map(Number), start_height: +m[3], end_height: +m[4] }
            : { front: [], back: [] };
    }

    (async function () {
        var lat;
        try { lat = await (await fetch("./latest.json")).json(); }
        catch (e) {
            $("summary").textContent = "Could not load draw data.";
            $("rows").innerHTML = '<tr class="empty-row"><td colspan="5">Couldn’t load draw data — please refresh.</td></tr>';
            return;
        }
        var head = lat.head || {};
        function renderSummary(h) {
            h = h || {};
            $("summary").textContent = h.count + " draws · algorithm " + h.algo_version
                + " · history head " + (h.head || "").slice(0, 16) + "…";
        }
        renderSummary(head);

        function renderHero(L) {
            if (L) {
                var lid = L.id | 0;
                $("lhId").textContent = "No. " + lid;
                $("lhBalls").innerHTML = '<span class="d-flex" role="group" aria-label="Front numbers">' + balls(L.front)
                    + '</span><span class="d-flex" role="group" aria-label="Back numbers">' + balls(L.back, "powerball") + '</span>';
                $("lhSrc").innerHTML = "derived from Bitcoin blocks <strong>" + (L.start_height | 0) + "–" + (L.end_height | 0) + "</strong>";
                $("lhVerify").href = "./verify.html?draw=" + lid; $("lhVerify").style.display = "";
                $("lhTime").textContent = L.timestamp ? String(L.timestamp) : "";
            } else {
                $("lhId").textContent = "—"; $("lhBalls").innerHTML = "";
                $("lhSrc").textContent = "No draws published yet — the first 144-block window is still being mined.";
                $("lhVerify").style.display = "none"; $("lhTime").textContent = "";
            }
        }
        var TABLE_RUN = 0;
        async function renderTable(latestId) {
            var myT = ++TABLE_RUN;
            try {
                var feed = await (await fetch("./feed.json")).json();
                if (myT !== TABLE_RUN) return;
                var items = (feed.items || []).filter(function (it) { return Number(it.id) !== latestId; }).slice(0, 20);
                $("rows").innerHTML = items.length ? items.map(function (it) {
                    var x = feedDraw(it), id = Number(it.id);
                    var when = it.date_published ? it.date_published.replace("T", " ").replace("Z", " UTC") : "";
                    return "<tr><td><a class='btn btn-sm' href='./verify.html?draw=" + id + "'>No. " + id + "</a></td>"
                        + "<td data-label='Front'><div class='d-flex' role='group' aria-label='Front numbers'>" + balls(x.front) + "</div></td>"
                        + "<td data-label='Back'><div class='d-flex' role='group' aria-label='Back numbers'>" + balls(x.back, "powerball") + "</div></td>"
                        + "<td data-label='Heights' class='text-secondary'>" + (x.start_height | 0) + " - " + (x.end_height | 0) + "</td>"
                        + "<td data-label='Time' class='text-secondary'>" + esc(when) + "</td></tr>";
                }).join("") : '<tr class="empty-row"><td colspan="5">' + (latestId != null ? 'No earlier draws yet — No. ' + (latestId | 0) + ' is the first.' : 'No draws published yet — the first 144-block window is still being mined.') + '</td></tr>';
            } catch (e) {
                if (myT !== TABLE_RUN) return;
                $("rows").innerHTML = '<tr class="empty-row"><td colspan="5">Couldn’t load the draws list — please refresh.</td></tr>';
            }
        }

        var latestId = (lat.latest && lat.latest.id != null) ? lat.latest.id : head.draw_id;
        renderHero(lat.latest);
        renderTable(latestId != null && latestId >= 0 ? Number(latestId) : null);
        sourceHealth();
        setInterval(sourceHealth, 120000);
        async function refreshNext() {
            try {
                var l = await (await fetch("./latest.json")).json();
                var id = (l.latest && l.latest.id != null) ? l.latest.id : (l.head && l.head.draw_id);
                if (id != null && id >= 0 && id !== latestId) {
                    latestId = id;
                    renderSummary(l.head);
                    renderHero(l.latest);
                    renderTable(Number(id));
                }
            } catch (e) { /* keep the last good view */ }
            if (latestId != null && latestId >= 0) nextDraw(latestId);
        }
        if (latestId != null && latestId >= 0) {
            nextDraw(latestId);
            setInterval(tick, 1000);
            setInterval(refreshNext, 120000);
        }
    })();
})();
