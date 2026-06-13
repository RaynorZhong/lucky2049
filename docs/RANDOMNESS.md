# Verifiable Randomness (coin & dice demo)

> 🌏 **English** · [中文](zh/RANDOMNESS.md)

A small, **informative** companion to the draw — **not** part of the frozen draw algorithm
([`SPEC.md`](../SPEC.md)) and **not a game** (no betting, money, or stakes). It shows that the same
public-seed machinery behind the lottery turns one immutable Bitcoin block hash into reproducible,
**unbiased** coin flips and dice rolls that anyone can recompute.

Served in the browser at [`/randomness.html`](https://lucky2049.com/randomness.html); reference
implementations are [`randomness.py`](../randomness.py) (Python, stdlib) and
[`static/randomness.js`](../static/randomness.js) (in-browser JS), kept bit-for-bit identical by a
Node parity test ([`tests/test_random_js.py`](../tests/test_random_js.py)).

## Algorithm

Input: a Bitcoin block hash `H` — a 64-character lowercase hex string (whitespace/case are normalized).

```
seed         = SHA-256( ascii(H) )                              # 32 bytes (hash taken as ASCII, like the draw seed)
stream(d)    = HMAC-SHA-256( seed, ascii("d:0") )               # 32 bytes
             ‖ HMAC-SHA-256( seed, ascii("d:1") ) ‖ …           # concatenated, domain d ∈ {"coin","dice"}
```

**Coin flips.** Flip `j` (0-indexed) is bit `j` of `stream("coin")`, taken **MSB-first within each
byte**: `bit = (stream[j // 8] >> (7 - (j mod 8))) & 1`; `1 → Heads`, `0 → Tails`. A single uniform
bit is exactly fair (P = ½), so no rejection is needed.

**Dice (d6).** Read bytes from `stream("dice")` in order. For a byte `b`: if `b < 252` the roll is
`b mod 6 + 1`; otherwise (`b ∈ {252,…,255}`) **reject it and read the next byte**. Since `252 = 6×42`,
every face 1–6 is backed by exactly 42 byte-values, so the result is an **unbiased** d6 (this
rejection is what removes the modulo bias a plain `b mod 6` would introduce). For a general
`sides`-sided die the reject threshold is `256 − (256 mod sides)`.

Both outputs are **prefix-stable**: asking for more flips/rolls never changes the earlier ones, and
the two domains are independent (changing the number of coins does not shift the dice).

## Test vectors

For the **Bitcoin genesis block** hash
`000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f`:

| Output | Value |
| --- | --- |
| `seed` | `09f663de96be771f50cab5ded00256ffe63773e2eaa9a604092951cc3d7c6621` |
| first 24 coin flips | `T H H H T T H T H H H H H H H H T T T T H H H H` |
| first 16 dice rolls | `3 3 5 6 1 2 2 3 1 4 3 4 5 4 6 2` |

## Reproduce it

```shell
python randomness.py        # prints the seed, 20 coin flips and 12 dice rolls for the genesis hash
```

Or in any JS runtime (the browser page does exactly this):

```js
const R = require('./static/randomness.js');     // reuses verify.js's SHA-256/HMAC
R.coinFlips('000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f', 24);
R.diceRolls('000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f', 16);
```
