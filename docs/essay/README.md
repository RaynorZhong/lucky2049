# Reference papers

Background reading on **Bitcoin / blockchain as a public randomness source** — the
prior art behind this project's draw beacon and its grind-resistance argument
(`SPEC.md` §8). The PDFs themselves are *not* committed (third-party academic papers:
binary bloat + copyright); this file maps each to its canonical, citable source so
the references survive in git without the files.

| Paper | Authors | Year | Canonical source |
|---|---|---|---|
| On Bitcoin as a Public Randomness Source | J. Bonneau, J. Clark, S. Goldfeder | 2015 | IACR ePrint [2015/1015](https://eprint.iacr.org/2015/1015) |
| A Random Zoo: sloth, unicorn, and trx | A. K. Lenstra, B. Wesolowski | 2015 | IACR ePrint [2015/366](https://eprint.iacr.org/2015/366) |
| Bitcoin Beacon | I. Bentov, A. Gabizon, D. Zuckerman | 2016 | arXiv [1605.04559](https://arxiv.org/abs/1605.04559) |
| Malleability of the Blockchain's Entropy | C. Pierrot, B. Wesolowski | 2016 | IACR ePrint [2016/370](https://eprint.iacr.org/2016/370) · *Cryptogr. Commun.* 10:211–233 (2018) |
| Note on Fair Coin Toss via Bitcoin | A. Back, I. Bentov | 2014 | arXiv [1402.3698](https://arxiv.org/abs/1402.3698) |

## Why they matter here

- **Bonneau, Clark & Goldfeder** — the foundational "Bitcoin as a beacon" analysis:
  lower-bounds the min-entropy per block and shows any attack on the beacon *is* an
  attack on Bitcoin with a boundable monetary cost. This is the paper cited in
  `SPEC.md` §8 (the economic-security / grind-resistance bound).
- **Lenstra & Wesolowski (sloth/unicorn/trx)** and **Bitcoin Beacon** — how to turn a
  manipulable public source into an *uncontestable* one (slow hashing; delay/commit
  constructions), and the limits of doing so against a well-funded adversary.
- **Pierrot & Wesolowski** and **Back & Bentov** — the attack side: how a miner with
  bounded budget can bias blockchain-derived entropy, which is exactly the threat the
  `W < B/p` bound in `SPEC.md` §8 is reasoning about.

To read them, drop the PDFs back into this directory (they're gitignored) or fetch
them from the links above.
