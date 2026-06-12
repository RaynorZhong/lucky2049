/*
 * lucky2049 in-browser verifier (algorithm v1, see SPEC.md).
 *
 * Pure JavaScript: self-contained SHA-256 + HMAC-SHA256, no external scripts and
 * no Web Crypto (window.crypto.subtle needs a secure context, which a plain-HTTP
 * site is not). Not depending on any third party is also the point — a verifier
 * you have to trust isn't a verifier. Recomputes a draw's numbers and its
 * tamper-evidence commitment entirely on the client.
 */
(function (root) {
  "use strict";

  // ---- SHA-256 (operates on / returns Uint8Array) ----
  var K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ]);

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  function sha256(data) {
    var h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
    var h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    var l = data.length;
    var withOne = l + 1;
    var pad = (56 - (withOne % 64) + 64) % 64;
    var total = withOne + pad + 8;
    var m = new Uint8Array(total);
    m.set(data, 0);
    m[l] = 0x80;
    var bitLen = l * 8;
    var dv = new DataView(m.buffer);
    dv.setUint32(total - 8, Math.floor(bitLen / 0x100000000));
    dv.setUint32(total - 4, bitLen >>> 0);
    var w = new Uint32Array(64);
    for (var off = 0; off < total; off += 64) {
      for (var i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
      for (i = 16; i < 64; i++) {
        var s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        var s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
      for (i = 0; i < 64; i++) {
        var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var ch = (e & f) ^ (~e & g);
        var t1 = (h + S1 + ch + K[i] + w[i]) | 0;
        var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + maj) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
    }
    var out = new Uint8Array(32);
    var odv = new DataView(out.buffer);
    var hs = [h0, h1, h2, h3, h4, h5, h6, h7];
    for (i = 0; i < 8; i++) odv.setUint32(i * 4, hs[i] >>> 0);
    return out;
  }

  function concat(a, b) {
    var out = new Uint8Array(a.length + b.length);
    out.set(a, 0); out.set(b, a.length);
    return out;
  }

  function hmacSha256(key, msg) {
    var B = 64;
    if (key.length > B) key = sha256(key);
    var k = new Uint8Array(B); k.set(key);
    var ipad = new Uint8Array(B), opad = new Uint8Array(B);
    for (var i = 0; i < B; i++) { ipad[i] = k[i] ^ 0x36; opad[i] = k[i] ^ 0x5c; }
    return sha256(concat(opad, sha256(concat(ipad, msg))));
  }

  // ---- helpers ----
  var enc = new TextEncoder();
  function utf8(s) { return enc.encode(s); }
  function hex(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += (bytes[i] + 0x100).toString(16).slice(1);
    return s;
  }
  function bigIntFromBytes(b) {
    var x = 0n;
    for (var i = 0; i < b.length; i++) x = (x << 8n) | BigInt(b[i]);
    return x;
  }

  // ---- algorithm v1 ----
  var BLUE_MAX = 35, BLUE_NUM = 5, RED_MAX = 12, RED_NUM = 2;

  function generate(hashes) {
    if (hashes.length !== 144) throw new Error("expected 144 hashes, got " + hashes.length);
    var seed = sha256(utf8(hashes.join("")));
    var nums = [];
    for (var k = 0; k < BLUE_NUM + RED_NUM; k++) nums.push(bigIntFromBytes(hmacSha256(seed, utf8(String(k)))));
    var pool, picks, i, idx;
    pool = []; for (i = 1; i <= BLUE_MAX; i++) pool.push(i);
    var front = [];
    for (i = 0; i < BLUE_NUM; i++) { idx = Number(nums[i] % BigInt(pool.length)); front.push(pool.splice(idx, 1)[0]); }
    front.sort(function (x, y) { return x - y; });
    pool = []; for (i = 1; i <= RED_MAX; i++) pool.push(i);
    var back = [];
    for (i = 0; i < RED_NUM; i++) { idx = Number(nums[BLUE_NUM + i] % BigInt(pool.length)); back.push(pool.splice(idx, 1)[0]); }
    back.sort(function (x, y) { return x - y; });
    return { front: front, back: back, seedHex: hex(seed) };
  }

  function commitmentFor(prevHex, drawId, algo, seedHex, front, back, start, end) {
    var payload = [prevHex, String(drawId), String(algo), seedHex,
      front.join(","), back.join(","), String(start), String(end)].join("|");
    return hex(sha256(utf8(payload)));
  }

  // ---- tamper-evidence chain ----
  var GENESIS_PREV = "0000000000000000000000000000000000000000000000000000000000000000";

  // Linkage scan over a published index ({draws:[...], head:{...}}). Each draw's
  // prev_commitment must equal the PREVIOUS record's commitment (draw 0 = the
  // genesis sentinel), and head.head must equal the head draw's commitment. This
  // is what makes a rewritten *middle* draw detectable -- recomputing one draw in
  // isolation only proves that draw, never that the records chain together.
  // Pure, in-memory; the verifier already has the whole index.
  function verifyChain(index) {
    var draws = (index && index.draws) || [];
    var byId = {};
    for (var i = 0; i < draws.length; i++) byId[draws[i].id] = draws[i];
    for (i = 0; i < draws.length; i++) {
      var d = draws[i];
      var expected = d.id === 0 ? GENESIS_PREV : (byId[d.id - 1] ? byId[d.id - 1].commitment : null);
      if (expected === null || d.prev_commitment !== expected) return { ok: false, brokenAt: d.id };
    }
    var head = (index && index.head) || {};
    if (head.head && byId[head.draw_id] && head.head !== byId[head.draw_id].commitment) {
      return { ok: false, brokenAt: "head" };
    }
    return { ok: true, brokenAt: null };
  }

  var api = { sha256: sha256, hmacSha256: hmacSha256, hex: hex, generate: generate,
    commitmentFor: commitmentFor, verifyChain: verifyChain, GENESIS_PREV: GENESIS_PREV };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Lucky2049Verify = api;
})(typeof window !== "undefined" ? window : globalThis);
