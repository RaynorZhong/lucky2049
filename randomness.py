#!/usr/bin/env python3
"""
randomness.py — verifiable coin flips & dice rolls from a Bitcoin block hash.

An educational companion to the frozen draw engine (verify.py / SPEC.md), NOT a
game: there is no betting, money, or wager here. It demonstrates that the same
public-seed / HMAC-stream / rejection-sampling machinery that picks the lottery
numbers also turns any single, immutable Bitcoin block hash into reproducible,
*unbiased* coin and dice outcomes that anyone can recompute and check.

Algorithm (see docs/RANDOMNESS.md; separate from the FROZEN draw spec):
  seed          = SHA256(ascii(block_hash))                       # 32 bytes
  stream(d)     = HMAC-SHA256(seed, ascii(f"{d}:{i}"))  for i=0,1,2,...  (concatenated bytes)
  coin flip j   = bit j of stream("coin"), MSB-first per byte; 1 -> H, 0 -> T   (a fair bit)
  dice roll     = next byte b of stream("dice") with b < 252; value = b % 6 + 1 (rejection -> unbiased d6)

Stdlib only. MUST stay bit-for-bit identical to static/randomness.js (Node parity
test in tests/test_random_js.py).
"""
import hashlib
import hmac


def _normalize(block_hash):
    h = block_hash.strip().lower()
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"block hash must be 64 hex chars, got {block_hash!r}")
    return h


def seed_for(block_hash):
    """SHA-256 of the 64-hex block hash taken as ASCII (same convention as the draw seed)."""
    return hashlib.sha256(_normalize(block_hash).encode("ascii")).digest()


def _stream_bytes(seed, domain, count):
    """First `count` bytes of HMAC-SHA256(seed, "domain:i") for i = 0, 1, 2, ..."""
    out = bytearray()
    i = 0
    while len(out) < count:
        out += hmac.new(seed, f"{domain}:{i}".encode("ascii"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:count])


def coin_flips(block_hash, n):
    """`n` fair coin flips. Flip j is bit j (MSB-first) of the 'coin' stream: 1 -> 'H', 0 -> 'T'."""
    if n < 0:
        raise ValueError("n must be >= 0")
    seed = seed_for(block_hash)
    buf = _stream_bytes(seed, "coin", (n + 7) // 8)
    return ["H" if (buf[j // 8] >> (7 - (j % 8))) & 1 else "T" for j in range(n)]


def _dice_from_seed(seed, m, sides=6):
    """`m` unbiased die rolls in 1..sides from a 32-byte seed. Reads bytes from the
    'dice' stream and rejection-samples (drops b >= 256 - 256 % sides) for no modulo bias."""
    if m < 0:
        raise ValueError("m must be >= 0")
    if not 2 <= sides <= 256:
        raise ValueError("sides must be in 2..256")
    limit = 256 - (256 % sides)   # 252 for d6
    out, i = [], 0
    while len(out) < m:
        for b in hmac.new(seed, f"dice:{i}".encode("ascii"), hashlib.sha256).digest():
            if b < limit:
                out.append(b % sides + 1)
                if len(out) == m:
                    break
        i += 1
    return out


def dice_rolls(block_hash, m, sides=6):
    """`m` unbiased die rolls seeded by a single block hash."""
    return _dice_from_seed(seed_for(block_hash), m, sides)


# ---- beacon series: one coin per block, one die per 6-block window (mirrors the draw) ----
def window_seed(hashes):
    """Seed for a multi-block window: SHA-256 of the concatenated hashes (like the
    draw, which concatenates its 144). `hashes` is an ordered list of 64-hex strings."""
    if not hashes:
        raise ValueError("a window needs at least one block hash")
    return hashlib.sha256("".join(_normalize(h) for h in hashes).encode("ascii")).digest()


def coin_for_block(block_hash):
    """One fair coin flip from a single block's hash (the per-block series)."""
    return coin_flips(block_hash, 1)[0]


def dice_for_window(hashes, sides=6):
    """One die from a window of block hashes (the per-6-blocks series)."""
    return _dice_from_seed(window_seed(hashes), 1, sides)[0]


if __name__ == "__main__":
    # Quick self-demo on the Bitcoin genesis block hash.
    GENESIS = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    print("seed :", seed_for(GENESIS).hex())
    print("coins:", " ".join(coin_flips(GENESIS, 20)))
    print("dice :", " ".join(map(str, dice_rolls(GENESIS, 12))))
