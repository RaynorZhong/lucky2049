# Commitment-head anchors (OpenTimestamps)

Each `NNNNNN.head.json` records the lucky2049 **commitment head** at draw
`NNNNNN` — a 32-byte hash that commits to the entire draw history up to that
point (see `SPEC.md` / the tamper-evidence section of the README). The companion
`NNNNNN.head.json.ots` is an [OpenTimestamps](https://opentimestamps.org) proof
that timestamps that file onto the **Bitcoin blockchain**.

Because the head is anchored to Bitcoin at a known time, the operator cannot
later rewrite past draws and recompute a consistent head: the old, anchored head
is provably older than any such rewrite. This is the external, trustless anchor
that completes the tamper-evidence story (the code provides the chain).

Produced daily by [`.github/workflows/anchor-head.yml`](../.github/workflows/anchor-head.yml),
dispatched by the Cloudflare Worker at 02:35 UTC (GitHub's 02:40 schedule is the fallback),
and also served at `https://lucky2049.com/anchors/NNNNNN.head.json` (+ `.ots`). The site has no
directory listing — this directory on GitHub is the list of anchored draws.

## Verify an anchor

Both the CI gate (`python scripts/accept_anchors.py --verify-chain`) and the browser
verifier (`https://lucky2049.com/verify.html`) evaluate `.ots` proofs directly to their
32-byte Bitcoin block header `merkle_root` using zero third-party packages.

You can also verify any proof with the upstream OpenTimestamps CLI:

```shell
pip install opentimestamps-client
ots verify anchors/006612.head.json.ots   # confirms the head existed by a Bitcoin block time
ots info   anchors/006612.head.json.ots   # inspect the proof
```

`ots verify` checks that the `.head.json` next to the `.ots` is the file that was
stamped, then reports the Bitcoin block (and time) the timestamp is anchored to.

A freshly created proof is **pending** (committed to the OTS calendars) until it
is confirmed in a Bitcoin block (~a few hours); `python scripts/anchor_head.py --upgrade`
upgrades pending proofs once on the next daily run and skips already-attested files.
