/*
 * lucky2049 — verifiable coin flips & dice rolls from a Bitcoin block hash.
 *
 * An educational companion to the verifier (verify.js), NOT a game: no betting,
 * money, or wager. It reuses verify.js's pure-JS SHA-256 / HMAC-SHA256 (no Web
 * Crypto, works on plain HTTP) to turn one immutable block hash into reproducible,
 * UNBIASED coin and dice outcomes anyone can recompute. See docs/RANDOMNESS.md.
 * MUST stay bit-for-bit identical to randomness.py (Node parity test).
 */
(function (root) {
  "use strict";
  var V = (typeof module !== "undefined" && module.exports) ? require("./verify.js") : root.Lucky2049Verify;

  // Minimal UTF-8 encoder (inputs here are ASCII: hex hashes + "coin:i"/"dice:i").
  function utf8(s) {
    var b = [];
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c < 0x80) b.push(c);
      else if (c < 0x800) b.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
      else b.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    }
    return new Uint8Array(b);
  }

  function normalize(blockHash) {
    var h = String(blockHash).trim().toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(h)) throw new Error("block hash must be 64 hex chars");
    return h;
  }

  // seed = SHA256(ascii(block_hash)) -- same "hex as ASCII" convention as the draw.
  function seedFor(blockHash) { return V.sha256(utf8(normalize(blockHash))); }

  // First `count` bytes of HMAC-SHA256(seed, "domain:i") for i = 0, 1, 2, ...
  function streamBytes(seed, domain, count) {
    var out = new Uint8Array(count), filled = 0, i = 0;
    while (filled < count) {
      var blk = V.hmacSha256(seed, utf8(domain + ":" + i));
      for (var k = 0; k < blk.length && filled < count; k++) out[filled++] = blk[k];
      i++;
    }
    return out;
  }

  // n fair coin flips: flip j is bit j (MSB-first per byte) of the 'coin' stream.
  function coinFlips(blockHash, n) {
    if (n < 0) throw new Error("n must be >= 0");
    var buf = streamBytes(seedFor(blockHash), "coin", (n + 7) >> 3);
    var out = [];
    for (var j = 0; j < n; j++) out.push(((buf[j >> 3] >> (7 - (j & 7))) & 1) ? "H" : "T");
    return out;
  }

  // m unbiased die rolls in 1..sides: byte b of the 'dice' stream, reject b >= 256-256%sides.
  function diceRolls(blockHash, m, sides) {
    if (sides === undefined) sides = 6;   // an explicit bad `sides` (e.g. 0) must still be rejected, like randomness.py
    if (m < 0) throw new Error("m must be >= 0");
    if (sides < 2 || sides > 256) throw new Error("sides must be in 2..256");
    var seed = seedFor(blockHash), limit = 256 - (256 % sides), out = [], i = 0;
    while (out.length < m) {
      var blk = V.hmacSha256(seed, utf8("dice:" + i));
      for (var k = 0; k < blk.length && out.length < m; k++) {
        if (blk[k] < limit) out.push(blk[k] % sides + 1);
      }
      i++;
    }
    return out;
  }

  var api = { seedFor: seedFor, coinFlips: coinFlips, diceRolls: diceRolls, utf8: utf8, normalize: normalize };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Lucky2049Random = api;
})(typeof self !== "undefined" ? self : this);
