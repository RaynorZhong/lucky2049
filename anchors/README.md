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

Produced weekly by [`.github/workflows/anchor-head.yml`](../.github/workflows/anchor-head.yml)
and also served at `https://lucky2049.com/anchors/`.

## Verify an anchor

```shell
pip install opentimestamps-client
ots verify anchors/006612.head.json.ots   # confirms the head existed by a Bitcoin block time
ots info   anchors/006612.head.json.ots   # inspect the proof
```

`ots verify` checks that the `.head.json` next to the `.ots` is the file that was
stamped, then reports the Bitcoin block (and time) the timestamp is anchored to.

A freshly created proof is **pending** (committed to the OTS calendars) until it
is confirmed in a Bitcoin block (~a few hours); `ots upgrade <file>.ots` then
fills in the full attestation. The workflow upgrades pending proofs on each run.
