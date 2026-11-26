(function () {
    "use strict";
    var Rnd = window.Lucky2049Random;
    var $ = function (id) { return document.getElementById(id); };
    var REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // ---- config ----
    var COIN_N = 10, DICE_M = 4, WINDOW = 6, POLL_MS = 60000;
    var COIN_MAX = 200, DICE_MAX = 40;
    var NEEDED = Math.max(COIN_N, DICE_M * WINDOW) + WINDOW + 2;
    var EXPLORERS = ["https://mempool.space/api", "https://blockstream.info/api"];
    var SPIN_SPEED = 360 / 1400;
    var REVEAL_MS = 3000;
    var REVEAL_DEG = REVEAL_MS * SPIN_SPEED;

    // ---- state ----
    var blocks = {};
    var tip = null;
    var shownCoins = [];
    var shownWindows = [];
    var coinHeads = 0;
    var coinQ = [], dieQ = [];
    var coinSp = null, dieSp = null, rafId = null;
    var lastUpdate = 0, polling = false;

    // ---- coin / die elements (visual only; values are deterministic) ----
    var PIPS = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
    function setPips(die, v) {
        for (var k = 0; k < 9; k++) die.children[k].className = "pip" + (PIPS[v].indexOf(k) >= 0 ? " on" : "");
    }
    var CLOVER = '<svg class="clover" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">'
        + '<circle cx="12" cy="6" r="6"/>'
        + '<circle cx="6" cy="12" r="6"/>'
        + '<circle cx="18" cy="12" r="6"/>'
        + '<circle cx="12" cy="18" r="6"/>'
        + '</svg>';
    function coinInner() {
        var R = 24, N = 16, html = '<div class="cface front"></div><div class="cface back">' + CLOVER + '</div><div class="cedge">';
        for (var i = 0; i < N; i++) html += '<i class="rim" style="transform:rotateZ(' + (i * 360 / N) + 'deg) translateY(' + R + 'px) rotateX(90deg)"></i>';
        return html + '</div>';
    }
    function pendingCoin() {
        var c = document.createElement("div");
        c.className = "bcoin pending"; c.setAttribute("role", "img"); c.setAttribute("aria-label", "waiting for the next block");
        c.title = "waiting for the next block…";
        c.innerHTML = coinInner();
        return c;
    }
    function pendingDie() {
        var d = document.createElement("div");
        d.className = "bdie pending"; d.setAttribute("role", "img"); d.setAttribute("aria-label", "waiting for the next 6-block window");
        d.title = "waiting for the next window…";
        for (var k = 0; k < 9; k++) d.appendChild(document.createElement("div")).className = "pip";
        setPips(d, 1 + (Math.random() * 6 | 0));
        return d;
    }

    // ---- derivations (reuse the shared, tested algorithm) ----
    function coinFace(h) { return Rnd.coinForBlock(blocks[h]); }
    function windowHashes(k) { var a = []; for (var b = WINDOW * k; b <= WINDOW * k + WINDOW - 1; b++) a.push(blocks[b]); return a; }
    function windowComplete(k) { for (var b = WINDOW * k; b <= WINDOW * k + WINDOW - 1; b++) if (!blocks[b]) return false; return true; }

    function targetCoins() {
        var lo = shownCoins.length ? shownCoins[shownCoins.length - 1] + 1 : tip - COIN_N + 1, a = [];
        for (var h = Math.max(lo, 0); h <= tip; h++) if (blocks[h]) a.push(h);
        return a;
    }
    function targetDice() {
        var kLast = Math.floor((tip - (WINDOW - 1)) / WINDOW);
        var lo = shownWindows.length ? shownWindows[shownWindows.length - 1] + 1 : kLast - DICE_M + 1, a = [];
        for (var k = Math.max(lo, 0); k <= kLast; k++) if (windowComplete(k)) a.push(k);
        return a;
    }
    function enqueue() {
        targetCoins().forEach(function (h) { if (shownCoins.indexOf(h) < 0 && coinQ.indexOf(h) < 0) coinQ.push(h); });
        targetDice().forEach(function (k) { if (shownWindows.indexOf(k) < 0 && dieQ.indexOf(k) < 0) dieQ.push(k); });
    }

    function spawnCoinSpinner() {
        var coin = pendingCoin(), cap = document.createElement("figcaption");
        cap.className = "coin-cap";
        cap.innerHTML = '<span class="coin-no">next</span>';
        var cell = document.createElement("figure");
        cell.className = "coin-cell pending";
        cell.appendChild(coin); cell.appendChild(cap);
        $("coins").appendChild(cell);
        coinSp = { el: coin, cell: cell, start: null, lockAngle: null };
    }
    function spawnDieSpinner() {
        var die = pendingDie(), cap = document.createElement("figcaption");
        cap.className = "die-cap";
        cap.innerHTML = '<span class="die-no">next</span><span class="die-blk">forming…</span>';
        var cell = document.createElement("figure");
        cell.className = "die-cell pending";
        cell.appendChild(die); cell.appendChild(cap);
        $("dice").appendChild(cell);
        dieSp = { el: die, cell: cell, start: null, lockAt: null, shuffled: 0 };
    }
    function faceAngleNear(pref, a, faceDeg) {
        var t = Math.round((pref - faceDeg) / 360) * 360 + faceDeg;
        while (t < a + 90) t += 360;
        return t;
    }

    function lockCoin(h) {
        var face = coinFace(h), el = coinSp.el, cell = coinSp.cell;
        el.className = "bcoin";
        el.setAttribute("aria-label", (face === "H" ? "Heads" : "Tails") + ", block " + h);
        el.title = "block #" + h + " → " + (face === "H" ? "Heads" : "Tails");
        el.style.transform = "rotateY(" + (face === "T" ? 180 : 0) + "deg)";
        cell.classList.remove("pending");
        cell.querySelector(".coin-no").textContent = "#" + h;
        shownCoins.push(h);
        if (face === "H") coinHeads++;
        while (shownCoins.length > COIN_MAX) {
            shownCoins.shift();
            var oldCell = $("coins").querySelector(".coin-cell:not(.pending)");
            if (oldCell) {
                var oldc = oldCell.querySelector(".bcoin");
                if (oldc && (oldc.getAttribute("aria-label") || "").charAt(0) === "H") coinHeads--;
                $("coins").removeChild(oldCell);
            }
        }
        syncInfo();
    }
    function lockDie(k) {
        var lo = WINDOW * k, hi = lo + WINDOW - 1, v = Rnd.diceForWindow(windowHashes(k)),
            el = dieSp.el, cell = dieSp.cell;
        el.className = "bdie rolling";
        el.setAttribute("aria-label", "Die #" + k + " showing " + v + ", blocks " + lo + " to " + hi);
        el.title = "die #" + k + " · blocks #" + lo + "–#" + hi + " → " + v;
        setPips(el, v);
        cell.classList.remove("pending");
        cell.querySelector(".die-no").textContent = "#" + k;
        cell.querySelector(".die-blk").textContent = "#" + lo + "–#" + hi;
        shownWindows.push(k);
        while (shownWindows.length > DICE_MAX) {
            shownWindows.shift();
            var oldd = $("dice").querySelector(".die-cell:not(.pending)");
            if (oldd) $("dice").removeChild(oldd);
        }
        syncInfo();
    }

    function tick(ts) {
        if (coinSp) {
            if (coinSp.start === null) coinSp.start = ts;
            var a = (ts - coinSp.start) * SPIN_SPEED;
            if (coinSp.lockAngle === null && coinQ.length) {
                var faceDeg = coinFace(coinQ[0]) === "T" ? 180 : 0;
                coinSp.lockAngle = REDUCED ? a : faceAngleNear(Math.max(a, REVEAL_DEG), a, faceDeg);
            }
            if (coinSp.lockAngle !== null && a >= coinSp.lockAngle) { lockCoin(coinQ.shift()); spawnCoinSpinner(); }
            else if (!REDUCED) coinSp.el.style.transform = "rotateY(" + (a % 360) + "deg)";
        }
        if (dieSp) {
            if (dieSp.start === null) dieSp.start = ts;
            if (dieSp.lockAt === null && dieQ.length) dieSp.lockAt = REDUCED ? ts : Math.max(ts, dieSp.start + REVEAL_MS);
            if (!REDUCED && ts - dieSp.shuffled > 90) { setPips(dieSp.el, 1 + (Math.random() * 6 | 0)); dieSp.shuffled = ts; }
            if (dieSp.lockAt !== null && ts >= dieSp.lockAt) { lockDie(dieQ.shift()); spawnDieSpinner(); }
        }
        rafId = requestAnimationFrame(tick);
    }
    function syncInfo() {
        if (shownCoins.length) {
            $("coinInfo").textContent = "newest: block #" + shownCoins[shownCoins.length - 1] + "  ·  " + shownCoins.length
                + " shown: " + coinHeads + " H · " + (shownCoins.length - coinHeads) + " T";
        }
        if (shownWindows.length) {
            var k0 = shownWindows[shownWindows.length - 1], have = (tip % WINDOW) + 1;
            var forming = have < WINDOW ? "  ·  next die forming: " + have + "/" + WINDOW + " blocks" : "";
            $("diceInfo").textContent = "newest die #" + k0 + " — blocks #" + (WINDOW * k0) + "–#" + (WINDOW * k0 + WINDOW - 1)
                + "  ·  " + shownWindows.length + " shown" + forming;
        }
    }
    function setLive(text, live) {
        var el = $("live"); el.textContent = "";
        if (live) el.appendChild(Object.assign(document.createElement("span"), { className: "livedot" }));
        el.appendChild(document.createTextNode(text));
    }
    function syncLive() {
        var age = lastUpdate ? Math.round((Date.now() - lastUpdate) / 1000) : 0;
        setLive("Live · tip block #" + tip + " · updated " + (age < 2 ? "just now" : age + "s ago"), true);
    }

    async function explorerNum(path) {
        var errs = [];
        for (var i = 0; i < EXPLORERS.length; i++) {
            try {
                var resp = await fetch(EXPLORERS[i] + path);
                if (!resp.ok) throw new Error("HTTP " + resp.status);
                var t = (await resp.text()).trim();
                if (!/^\d+$/.test(t)) throw new Error("unexpected response");
                return parseInt(t, 10);
            } catch (e) { errs.push(e.message); }
        }
        throw new Error("explorers failed (" + errs.join(", ") + ")");
    }
    async function fetchBlocks(top, count) {
        var out = [], h = top;
        while (out.length < count && h >= 0) {
            var arr = null, errs = [];
            for (var i = 0; i < EXPLORERS.length; i++) {
                try {
                    var resp = await fetch(EXPLORERS[i] + "/blocks/" + h);
                    if (!resp.ok) throw new Error("HTTP " + resp.status);
                    var a = await resp.json();
                    if (!Array.isArray(a) || !a.length) throw new Error("bad shape");
                    arr = a; break;
                } catch (e) { errs.push(e.message); }
            }
            if (!arr) throw new Error("explorers failed at " + h + " (" + errs.join(", ") + ")");
            for (var j = 0; j < arr.length && out.length < count; j++) {
                var b = arr[j];
                if (typeof b.height === "number" && b.height <= h && /^[0-9a-f]{64}$/.test(b.id || "")) out.push({ height: b.height, id: b.id.toLowerCase() });
            }
            var nxt = arr[arr.length - 1].height - 1;
            if (nxt >= h) break;
            h = nxt;
        }
        return out;
    }
    function trimBlocks() {
        var low = tip - NEEDED;
        for (var hk in blocks) if (+hk < low) delete blocks[hk];
        coinQ = coinQ.filter(function (h) { return blocks[h]; });
        dieQ = dieQ.filter(windowComplete);
    }

    async function load() {
        tip = await explorerNum("/blocks/tip/height");
        var got = await fetchBlocks(tip, NEEDED);
        blocks = {}; got.forEach(function (b) { blocks[b.height] = b.id; });
        shownCoins = []; shownWindows = []; coinQ = []; dieQ = []; coinHeads = 0;
        coinSp = null; dieSp = null;
        $("coins").innerHTML = ""; $("dice").innerHTML = "";
        spawnCoinSpinner(); spawnDieSpinner();
        enqueue();
        syncInfo(); lastUpdate = Date.now(); syncLive();
    }
    async function poll() {
        if (polling) return;
        polling = true;
        try {
            var newTip = await explorerNum("/blocks/tip/height");
            if (newTip < tip) { lastUpdate = Date.now(); syncLive(); return; }
            if (newTip === tip) {
                var topBlocks = await fetchBlocks(tip, WINDOW + 2);
                if (topBlocks.some(function (b) { return blocks[b.height] && blocks[b.height] !== b.id; })) {
                    setLive("reorg — reloading…", false); await load(); return;
                }
                lastUpdate = Date.now(); syncLive(); return;
            }
            var got = await fetchBlocks(newTip, Math.max(newTip - tip + 2, WINDOW + 2));
            var reorg = got.some(function (b) { return blocks[b.height] && blocks[b.height] !== b.id; });
            if (reorg) { setLive("reorg — reloading…", false); await load(); return; }
            got.forEach(function (b) { blocks[b.height] = b.id; });
            tip = newTip; trimBlocks();
            enqueue();
            syncInfo(); lastUpdate = Date.now(); syncLive();
        } catch (e) {
            /* transient fetch failure -- keep the last good view, try again next tick */
        } finally {
            polling = false;
        }
    }

    (async function start() {
        spawnCoinSpinner(); spawnDieSpinner();
        rafId = requestAnimationFrame(tick);
        setLive("Reading the latest blocks…", false);
        for (var ok = false, tries = 0; !ok; tries++) {
            try { await load(); ok = true; }
            catch (e) {
                setLive("Could not reach the chain — retrying…", false);
                await new Promise(function (r) { setTimeout(r, Math.min(8000, 2000 + tries * 2000)); });
            }
        }
        setInterval(poll, POLL_MS);
        setInterval(function () { if (lastUpdate) syncLive(); }, 1000);
        document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
    })();
    void rafId;
})();
