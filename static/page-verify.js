var V = window.Lucky2049Verify;
var $ = function (id) { return document.getElementById(id); };
var INDEX = null;  // cached index.json
var INDEX_CHAIN = null;  // full snapshot linkage result, cached with INDEX

function setBadge(el, ok) { el.className = "badge " + (ok ? "pass" : "fail"); el.textContent = ok ? "PASS" : "FAIL"; }
// The verdict hero: 'wait' | 'pass' | 'fail' | 'err'. Retry shows only on 'err'.
function setVerdict(state, text, sub) {
    $("verdict").className = "verdict " + state;
    $("vdIcon").textContent = state === "pass" ? "✓" : state === "fail" ? "✗" : state === "err" ? "!" : "…";
    $("vdText").textContent = text;
    $("vdSub").textContent = sub || "";
    $("vdRetry").style.display = state === "err" ? "" : "none";
}
// Pure decision: (result reproduced?, chain state 'pass'|'fail'|'unchecked', draw id) -> the
// verdict. Green ONLY when the numbers reproduce AND the commitment chain is verified. Kept
// pure (no DOM) so tests/test_verify_fetch_js.py can lock the whole truth table.
function composeVerdict(resultOk, chainState, id) {
    if (resultOk && chainState === "pass") {
        return { state: "pass", text: "This draw matches the Bitcoin chain.",
            sub: "Recomputed in your browser from the 144 block hashes"
                + (id === 0 ? " · genesis commitment verified." : " · commitment chain intact.") };
    }
    if (resultOk && chainState === "unchecked") {
        return { state: "err", text: "Commitment verification is incomplete.",
            sub: "The numbers match, but required commitment evidence is missing or invalid." };
    }
    return { state: "fail",
        text: resultOk ? "Tamper-evidence chain is BROKEN." : "This draw does NOT match the chain.",
        sub: resultOk ? "the numbers match, but the commitment doesn't link to the previous draw."
                      : "the recomputed numbers differ from the published result." };
}
function balls(c, nums, extra) {
    c.innerHTML = "";
    (nums || []).forEach(function (n) { var d = document.createElement("div"); d.className = "white-balls" + (extra ? " " + extra : ""); d.textContent = n; c.appendChild(d); });
}
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function isCommitment(value) { return typeof value === "string" && value.length === 64 && /^[0-9a-f]{64}$/.test(value); }

var INDEX_P = null;  // in-flight/resolved load promise -- dedups concurrent callers
var RELOADED_FOR = null;  // one-shot CDN cache-skew retry guard per requested draw id
function loadIndex(bust) {
    // Promise-memoized unless `bust` requests a one-shot reload (?v=<id>) when
    // a newly published draw is ahead of a cached GitHub Pages index.json edge copy.
    if (INDEX_P && bust === undefined) return INDEX_P;
    var url = bust === undefined ? "./index.json" : "./index.json?v=" + encodeURIComponent(bust);
    INDEX_P = (async function () {
        var idx = await (await fetch(url)).json();
        var h = idx.head || {};
        var chain = V.verifyChain(idx);  // full-chain linkage scan -- runs once per loaded snapshot
        $("vHead").textContent = (h.head || "(none)") + "  ·  " + (idx.count || 0) + " draws"
            + "  ·  chain " + (chain.ok ? "✓ intact" : "✗ BROKEN at " + chain.brokenAt);
        $("vHead").style.color = chain.ok ? "" : "#b91c1c";
        if (idx.count > 0 && $("drawId")) $("drawId").max = idx.count - 1;
        INDEX = idx;
        INDEX_CHAIN = chain;
        return idx;
    })();
    INDEX_P.catch(function () { INDEX_P = null; });  // let a failed load be retried
    return INDEX_P;
}

// Fetch the 144 block hashes for a height range from a public explorer
// (mempool.space then blockstream.info, both CORS-enabled).
var BATCH_APIS = [
    { name: "mempool.space", url: function (h) { return "https://mempool.space/api/blocks/" + h; } },
    { name: "blockstream.info", url: function (h) { return "https://blockstream.info/api/blocks/" + h; } }
];
var EXPLORERS = [
    { name: "mempool.space", url: function (h) { return "https://mempool.space/api/block-height/" + h; } },
    { name: "blockstream.info", url: function (h) { return "https://blockstream.info/api/block-height/" + h; } }
];
function progress(out, total) {
    var done = 0; for (var i = 0; i < out.length; i++) if (out[i]) done++;
    $("status").textContent = "Fetching block hashes from the chain… " + done + "/" + total;
}
async function fetchHashesBatched(start, end, out) {
    var total = end - start + 1;
    for (var e = 0; e < BATCH_APIS.length; e++) {
        for (var k = 0; k < total; k++) out[k] = undefined;   // fresh slots per explorer
        var clean = true;
        for (var top = end; top >= start && clean; top -= 10) {   // /blocks/{top} -> [top .. top-9]
            try {
                var resp = await fetch(BATCH_APIS[e].url(top));
                if (!resp.ok) throw new Error("HTTP " + resp.status);
                var page = await resp.json();
                if (!Array.isArray(page) || !page.length) throw new Error("bad shape");
                for (var j = 0; j < page.length; j++) {
                    var hgt = page[j].height, id = (page[j].id || "").toLowerCase();
                    if (typeof hgt !== "number" || !/^[0-9a-f]{64}$/.test(id)) { clean = false; break; }
                    if (hgt >= start && hgt <= end) out[hgt - start] = id;
                }
                progress(out, total);
            } catch (x) { clean = false; }   // this explorer can't serve the window cleanly
        }
        if (clean) {
            var allFilled = true;
            for (var m = 0; m < total; m++) if (!out[m]) { allFilled = false; break; }
            if (allFilled) return true;       // one explorer served every slot
        }
    }
    return false;   // no single explorer served the whole window -> per-height fallback
}
async function fetchHashAt(height, explorer) {
    var resp = await fetch(explorer.url(height));
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var hash = (await resp.text()).trim().toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(hash)) throw new Error("unexpected response");
    return hash;
}
async function fetchHashesPerHeight(start, end, out) {  // fallback: concurrency 8
    var total = end - start + 1, errs = [];
    for (var e = 0; e < EXPLORERS.length; e++) {
        for (var k = 0; k < total; k++) out[k] = undefined;
        var next = 0, clean = true, failure;
        async function worker() {
            while (clean && next < total) {
                var idx = next++;
                try {
                    var hash = await fetchHashAt(start + idx, EXPLORERS[e]);
                    if (clean) { out[idx] = hash; progress(out, total); }
                } catch (x) { clean = false; failure = x; }
            }
        }
        await Promise.all(Array.from({ length: 8 }, worker));
        if (clean) return;
        errs.push(EXPLORERS[e].name + " (" + failure + ")");
    }
    throw new Error("all explorers failed for heights " + start + ".." + end + ": " + errs.join(", "));
}
async function fetchHashes(start, end) {
    var out = new Array(end - start + 1);
    if (await fetchHashesBatched(start, end, out)) return out;
    for (var i = 0; i < out.length; i++) out[i] = undefined;  // drop partials, refetch cleanly
    await fetchHashesPerHeight(start, end, out);
    return out;
}

var RUN = 0;  // run token: a slow fetch from an earlier verify() must not write stale badges
async function verify(id) {
    var myRun = ++RUN;
    $("panel").style.display = "none";
    $("status").textContent = "Loading…";
    var index;
    try { index = await loadIndex(); } catch (e) { $("status").textContent = "Could not load index.json: " + e; return; }
    var rec = (index.draws || []).find(function (d) { return d.id === id; });
    if (!rec && id >= (index.count || 0) && RELOADED_FOR !== id) {
        RELOADED_FOR = id;
        try { index = await loadIndex(id); } catch (e) { /* keep existing snapshot on reload error */ }
        rec = (index.draws || []).find(function (d) { return d.id === id; });
    }
    if (!rec) { $("status").textContent = "Draw " + id + " not found (have 0.." + (index.count - 1) + ")."; return; }

    var start = id * 144, end = id * 144 + 143;
    $("vId").textContent = id;
    $("vAlgo").textContent = rec.algo_version;
    $("vHeights").textContent = start + " … " + end + " (144 blocks)";
    balls($("vFront"), rec.front);
    balls($("vBack"), rec.back, "powerball");
    $("vCommit").textContent = rec.commitment || "(none)";
    $("vPrev").textContent = rec.prev_commitment || "(none)";
    $("vSeed").textContent = "(recomputing…)";
    ["bResult", "bChain"].forEach(function (b) { $(b).className = "badge wait"; $(b).textContent = "…"; });
    setVerdict("wait", "Recomputing from the Bitcoin chain…", "fetching the 144 block hashes…");
    $("panel").style.display = "block";

    var hashes;
    try { hashes = await fetchHashes(start, end); }
    catch (e) {
        if (myRun !== RUN) return;
        $("vSeed").textContent = "—";
        $("status").textContent = "Or run: python verify.py " + id + " --source mempool";
        setVerdict("err", "Couldn't reach the chain.",
            "the public explorers didn't answer (" + e + ") — check your connection and retry.");
        return;
    }
    if (myRun !== RUN) return;  // a newer verify() superseded this run -- don't write stale badges

    var r = V.generate(hashes);
    $("vSeed").textContent = r.seedHex;
    var resultOk = eq(r.front, rec.front) && eq(r.back, rec.back);
    setBadge($("bResult"), resultOk);
    var chainState = "unchecked";   // 'pass' | 'fail' | 'unchecked' (predecessor / commitment unavailable)
    var prevRec = id === 0 ? null : (index.draws || []).find(function (d) { return d.id === id - 1; });
    var linkedPrev = id === 0 ? V.GENESIS_PREV : (prevRec ? prevRec.commitment : null);
    if (INDEX_CHAIN && !INDEX_CHAIN.ok) {
        chainState = "fail";  // a local match cannot override a broken snapshot
    } else if (INDEX_CHAIN && isCommitment(rec.commitment) && isCommitment(linkedPrev)
               && isCommitment(rec.prev_commitment)) {
        var c = V.commitmentFor(linkedPrev, id, rec.algo_version, r.seedHex, r.front, r.back, start, end);
        chainState = c === rec.commitment && rec.prev_commitment === linkedPrev ? "pass" : "fail";
    }
    if (chainState === "unchecked") { $("bChain").className = "badge wait"; $("bChain").textContent = "N/A"; }
    else setBadge($("bChain"), chainState === "pass");

    var verdict = composeVerdict(resultOk, chainState, id);
    setVerdict(verdict.state, verdict.text, verdict.sub);
    $("status").textContent = "Recomputed locally — no server trust.";
}

var ANCHOR_APIS = ["https://mempool.space/api", "https://blockstream.info/api"];
async function fetchBlockMerkleRoot(height) {
    for (var i = 0; i < ANCHOR_APIS.length; i++) {
        try {
            var hr = await fetch(ANCHOR_APIS[i] + "/block-height/" + height);
            if (!hr.ok) continue;
            var bhash = (await hr.text()).trim().toLowerCase();
            if (!/^[0-9a-f]{64}$/.test(bhash)) continue;
            var br = await fetch(ANCHOR_APIS[i] + "/block/" + bhash);
            if (!br.ok) continue;
            var blk = await br.json();
            var mr = String((blk && blk.merkle_root) || "").toLowerCase();
            if (/^[0-9a-f]{64}$/.test(mr)) return mr;
        } catch (e) { /* try next explorer */ }
    }
    return null;
}
async function verifyAnchor() {
    var bEl = $("bAnchor"), vEl = $("vAnchor");
    bEl.className = "badge wait"; bEl.textContent = "…"; vEl.textContent = "checking /anchors/…";
    var idx = INDEX || await loadIndex();
    if (!INDEX_CHAIN || !INDEX_CHAIN.ok) {
        setBadge(bEl, false); vEl.textContent = "snapshot chain broken"; return;
    }
    var draws = idx.draws || [];
    if (!draws.length) { bEl.textContent = "N/A"; vEl.textContent = "no draws published"; return; }
    var topId = draws[draws.length - 1].id, minId = Math.max(0, topId - 5);
    var pendingId = null;
    for (var cid = topId; cid >= minId; cid--) {
        var pad = String(cid).padStart(6, "0");
        var hResp, oResp;
        try {
            hResp = await fetch("./anchors/" + pad + ".head.json");
            if (!hResp.ok) continue;
            oResp = await fetch("./anchors/" + pad + ".head.json.ots");
            if (!oResp.ok) continue;
        } catch (e) { continue; }
        var headBytes = new Uint8Array(await hResp.arrayBuffer());
        var otsBytes = new Uint8Array(await oResp.arrayBuffer());
        if (otsBytes.length > 65536) {
            setBadge(bEl, false); vEl.textContent = "oversized .ots proof for draw #" + cid; return;
        }
        var headDoc;
        try {
            if (headBytes.length > 4096) throw new Error("head file too large");
            var headStr = "";
            for (var bi = 0; bi < headBytes.length; bi++) headStr += String.fromCharCode(headBytes[bi]);
            headDoc = JSON.parse(headStr);
        }
        catch (e) { setBadge(bEl, false); vEl.textContent = "malformed " + pad + ".head.json"; return; }
        var targetDraw = draws[cid];
        if (!targetDraw || targetDraw.id !== cid || headDoc.draw_id !== cid || headDoc.head !== targetDraw.commitment) {
            setBadge(bEl, false); vEl.textContent = "anchor #" + cid + " does not match published chain"; return;
        }
        var parsed = V.verifyOtsProof(otsBytes, headBytes);
        if (!parsed.ok) {
            setBadge(bEl, false); vEl.textContent = "invalid .ots proof for draw #" + cid; return;
        }
        if (!parsed.attestations.length) {
            if (pendingId === null) pendingId = cid;
            continue;
        }
        var sawMismatch = false;
        for (var a = 0; a < parsed.attestations.length; a++) {
            var att = parsed.attestations[a];
            var chainMr = await fetchBlockMerkleRoot(att.height);
            if (chainMr === att.merkleRoot) {
                setBadge(bEl, true);
                vEl.textContent = "draw #" + cid + " anchored in Bitcoin block #" + att.height
                    + (pendingId !== null ? " (draw #" + pendingId + " awaiting confirmation)" : "");
                return;
            }
            if (chainMr !== null) sawMismatch = true;
        }
        if (sawMismatch) {
            setBadge(bEl, false);
            vEl.textContent = "merkle_root mismatch for draw #" + cid;
            return;
        }
        bEl.className = "badge wait"; bEl.textContent = "OFFLINE";
        vEl.textContent = "draw #" + cid + " proof parsed; explorers unreachable";
        return;
    }
    if (pendingId !== null) {
        bEl.className = "badge wait"; bEl.textContent = "PENDING";
        vEl.textContent = "draw #" + pendingId + " stamped · awaiting Bitcoin block confirmation";
    } else {
        bEl.className = "badge wait"; bEl.textContent = "N/A";
        vEl.textContent = "no recent anchor file served";
    }
}

function currentId() {
    var v = parseInt($("drawId").value, 10);
    if (!isNaN(v)) return v;
    return (INDEX && INDEX.count) ? INDEX.count - 1 : 0;   // empty box -> latest (matches the placeholder)
}
$("verifyForm").addEventListener("submit", function (e) { e.preventDefault(); history.replaceState(null, "", "?draw=" + currentId()); verify(currentId()); });
$("vdRetry").addEventListener("click", function () { verify(currentId()); });
var CLIP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>';
var CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 6"/></svg>';
function fallbackCopy(text) {
    try {
        var ta = document.createElement("textarea");
        ta.value = text; ta.setAttribute("readonly", "");
        ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        var ok = document.execCommand("copy");
        document.body.removeChild(ta);
        return ok;
    } catch (e) { return false; }
}
function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return fallbackCopy(text); });
    }
    return Promise.resolve(fallbackCopy(text));
}
["vSeed", "vCommit", "vPrev", "vHead"].forEach(function (id) {
    var span = $(id);
    var btn = document.createElement("button");
    btn.type = "button"; btn.className = "copy-btn"; btn.innerHTML = CLIP;
    btn.setAttribute("aria-label", "Copy " + id.slice(1).toLowerCase());
    btn.addEventListener("click", function () {
        var t = (span.textContent || "").trim();
        if (id === "vHead") t = t.split("  ·  ")[0];   // copy just the head hash, not the meta
        if (!t || t === "(none)" || t === "(recomputing…)" || t === "—" || t === "…") return;
        copyText(t).then(function (ok) {
            btn.innerHTML = ok ? CHECK : CLIP; btn.classList.toggle("done", ok);
            setTimeout(function () { btn.innerHTML = CLIP; btn.classList.remove("done"); }, 1200);
        });
    });
    span.parentNode.appendChild(btn);
});

var q = new URLSearchParams(location.search).get("draw");
if (q !== null) $("drawId").value = q;
loadIndex().then(function (idx) {
    var count = (idx && idx.count) || 0;
    if (count > 0) $("drawId").max = count - 1;
    var v = parseInt($("drawId").value, 10);
    if (!isNaN(v) && v >= 0) verify(v);
    else if (count > 0) { $("drawId").value = count - 1; verify(count - 1); }
    else $("status").textContent = "No draws published yet.";
    verifyAnchor();
}, function () { /* loadIndex surfaces its own error in the head panel */ });
