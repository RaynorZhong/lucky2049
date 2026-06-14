# lucky2049 Draw Algorithm Specification

> 🌏 **English** · [中文](docs/zh/SPEC.md)

**Algorithm Version: `v1`**
**Status: FROZEN**

This document is the normative definition of the lucky2049 draw system. Anyone who follows the
steps described here, using the same Bitcoin block hashes, can **reproduce any draw bit for bit**.

> "Frozen" means no byte of `v1` ever changes. If the rules must change in the future, a new
> version (`v2`, `v3`, …) is released that **applies only to draws published after it**;
> historical draws stay verifiable under the version they declared. Every draw declares the
> algorithm version it used (each record in the published `index.json` carries an `algo_version`
> field).

---

## 1. Game Parameters

The system mirrors **Super Lotto** rules:

| Area | Count | Range |
|------|----------|----------|
| front | 5 distinct | 1 – 35 |
| back  | 2 distinct | 1 – 12 |

Constants:
- `NUM_BLOCKCHAIN = 144` — the number of consecutive blocks each draw uses.
- `BLUE_BALL_MAX = 35`, `BLUE_BALL_NUM = 5`
- `RED_BALL_MAX = 12`, `RED_BALL_NUM = 2`

---

## 2. Input Selection (deterministic, genesis-anchored)

Draw `N` (`N` a non-negative integer, `draw_id = N`) uses the Bitcoin mainnet block height range:

```
[ N * 144 ,  N * 144 + 143 ]   (inclusive, 144 consecutive blocks)
```

For example: draw 0 = heights 0..143 (draw 0's first block is the Bitcoin genesis block);
draw 6315 = heights 909360..909503.

**This is a pure deterministic function of the draw id, anchored at the genesis block (height 0);
the operator has zero discretion over "which blocks" are used.** The rule itself is a permanent
commitment — no per-draw commitment of the height range is needed.

---

## 3. Block Hash Format

Each block's hash is its **canonical display hash**: a 64-character **lowercase hexadecimal**
string (the big-endian block hash shown by Bitcoin's `getblockhash` / every block explorer), e.g.:

```
000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f   ← height 0 (genesis block)
```

---

## 4. Generation Steps

### Step 1 — concatenate
Order the draw's 144 blocks by **ascending height** and join their 64-char lowercase-hex hashes
**with no separator** into `combined` (length = 144 × 64 = 9216 chars).

### Step 2 — seed
```
seed = SHA256( combined.encode("utf-8") )        # 32-byte raw digest, not a hex string
```

### Step 3 — deterministic RNG
For `counter = 0, 1, 2, …` in turn:
```
r_k = HMAC_SHA256( key = seed, msg = ascii(str(counter)) ).digest()   # 32 bytes
int_k = int.from_bytes(r_k, "big")                                    # 256-bit unsigned big-endian integer
```
A total of `BLUE_BALL_NUM + RED_BALL_NUM = 7` integers are needed, using `counter = 0..6`.

> Modulo-bias note: the dividend is a full 256-bit integer; the bias from reducing it modulo small
> numbers like 35 / 12 is on the order of `m / 2^256 ≈ 10^-75`, negligible — the distribution is
> effectively uniform.

### Step 4 — front
```
pool = [1, 2, …, 35]
front = []
for i in 0..4:
    idx = int_i mod len(pool)
    front.append( pool.pop(idx) )      # pop after picking, so numbers are distinct
front.sort()                           # ascending
```

### Step 5 — back
```
pool = [1, 2, …, 12]
back = []
for i in 0..1:
    idx = int_(5+i) mod len(pool)
    back.append( pool.pop(idx) )
back.sort()                            # ascending
```

### Output
`front` (5 ascending integers) and `back` (2 ascending integers).

---

## 5. Verification

Anyone can independently confirm a draw was not manipulated by:
1. obtaining the 144 block hashes for heights `N*144 .. N*144+143` from any trusted source
   (your own full node, `getblockhash`, or any block explorer);
2. recomputing per the steps in §4;
3. comparing against the system's published result for that draw (record N in `index.json`, or
   via `verify.py` / `verify.html`).

The repo's `verify.py` does this in one command.

**Bitcoin block hashes are determined by network-wide consensus — objective and tamper-proof** —
so the source of truth is the blockchain itself, not this system's database or any single API.
This is the foundation of the draw being "transparent and independently reproducible."

---

## 6. Test Vectors (v1)

| draw_id | heights | SHA256 seed (hex) | front | back |
|---------|----------|-------------------|-------|------|
| 0    | 0 .. 143         | `cbc014f38f94d72431f9e1d2f978ff3db74a0be3ffa0e8fcfc1af92818ea324c` | [11, 14, 19, 30, 35] | [2, 11] |
| 1    | 144 .. 287       | `a6fa3f9ae093938dd22eed0d25215a6b97bfaade8aeda3e1eef7038817279746` | [8, 10, 11, 18, 23]  | [6, 11] |
| 6315 | 909360 .. 909503 | `0c794f8b23f2dd36f702c9a3fe39d240a38ad04a8af3023fae706301fdba16a6` | [1, 22, 25, 30, 35]  | [4, 11] |

Draw 0's first block hash = the Bitcoin genesis block
`000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f`.

---

## 7. Residual Risk

This spec guarantees **reproducibility** and **zero operator discretion**, but does not guarantee
absolute immunity to **miner manipulation**: the miner who mines a given block has limited grinding
power over that block's hash. The system pushes the attack cost very high by **aggregating 144
consecutive blocks** (about a day) — an attacker would need to control a large fraction of those
blocks within ~24 hours and forgo enormous block rewards. This is **economic** security, not
absolute cryptographic security, and the residual risk grows with downstream prize size. For a
stronger guarantee, a VDF (verifiable delay function) can be layered on top of the seed.

---

## 8. Economic-Security Bound (informative · non-normative)

> This section is **informative** and is not part of the frozen `v1` algorithm; it does not affect
> any draw's reproducibility. It simply **quantifies** §7's note that "residual risk grows with
> downstream prize size."

The miner of a window's **last** block is the only party who can "grind" the result: each re-roll
costs them a whole block reward `B` (subsidy + fees), and the re-rolled result is not guaranteed to
favor them. For a prize of value `W` won with probability `p`, the expected gain of one grind is
about `p · W`. Grinding is profitable **iff**

```
p · W > B        ⟺        W > B / p
```

so **any downstream prize should stay below the ceiling**:

```
W_max = B / p
```

This game's jackpot (matching all 5 front + both back) has probability:

```
p_jackpot = 1 / ( C(35,5) · C(12,2) ) = 1 / ( 324,632 × 66 ) = 1 / 21,425,712
```

Taking the post-2024-halving block subsidy `B = 3.125 BTC` (halving again to 1.5625 around 2028):

```
W_max = 3.125 BTC × 21,425,712 ≈ 6.70 × 10^7 BTC
```

i.e. a single prize would have to reach roughly **67 million BTC** (trillions of USD at current
prices) before last-block grinding becomes profitable — far above any realistic prize. **So for
any realistically sized prize, this draw is economically secure as a public randomness beacon.**

- This is a **first-order economic-security** bound (single block, single grind), not an absolute
  cryptographic guarantee, consistent with §7; a VDF on top of the seed gives a stronger guarantee.
- **lucky2049 itself runs no prize pool, sells no tickets, and pays out nothing**; this ceiling
  only bounds **downstream** projects (the homepage links here from its footer rather than
  displaying a live `B / p` figure).
- Theoretical basis: Bonneau, Clark, Goldfeder, *On Bitcoin as a Public Randomness Source* (2015).
