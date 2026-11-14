# Test-Driven Development guide

> 🌏 **English** · [中文](zh/TDD.md)

This project is set up for a fast red → green → refactor loop.

## The loop

1. **Red** — write a test for the behavior you want; run it; watch it fail for
   the right reason (assertion, not an import error).
2. **Green** — write the minimum code to make it pass.
3. **Refactor** — clean up with the test as a safety net; keep it green.

Keep tests running on every save:

```shell
make install-dev    # one-time: pytest tooling into the venv (runtime is stdlib-only)
make watch          # re-runs the suite on every file change (the TDD loop)
```

Other commands:

```shell
make test           # run once
make cov            # run with a coverage report (term-missing)
./.venv/bin/python -m pytest tests/test_spec_v1.py -v     # one file
./.venv/bin/python -m pytest -k commitment                # by keyword
python -m unittest discover -s tests                      # stdlib-only fallback
```

## Where tests go

`tests/test_*.py`. Plain `unittest.TestCase` classes and pytest-style functions
both run under pytest, so use whichever fits.

## What the suite looks like

Everything is **stdlib + Node only** — no database, no heavy deps; the one fixture is the SPEC draw-0 window (`tests/fixtures/draw0_hashes.json`), which the golden-vector and JS-parity locks read.
The site is static, so the tests pin the two things that matter:

- **The frozen algorithm + commitment** — golden vectors in `tests/test_spec_v1.py`
  and `tests/test_commitment.py` recompute against `verify.py` and SPEC.md.
- **The in-browser JS** — `tests/test_verify_js.py`, `tests/test_stats_js.py`,
  `tests/test_random_js.py`, and `tests/test_trend_js.py` run `static/verify.js` /
  `static/stats.js` / `static/randomness.js` / `static/trend.js` under Node and check
  they reproduce the Python result (and frozen golden values). `tests/test_verify_fetch_js.py`
  likewise runs verify.html's inline page logic under Node — the 144-hash fetch/assembly +
  the pass/fail verdict. The Node-based tests self-skip if Node is absent. (Page structure —
  the shared nav, the stats read, the homepage de-dup, the homepage's next-draw ETA target —
  is guarded by the stdlib `tests/test_pages.py` and `tests/test_index_js.py` text checks.)

`tests/test_trigger_worker.py` runs the external trigger (`ops/trigger-worker`) under Node with a
stubbed `fetch`: exactly one authenticated dispatch, one retry on transient errors only, loud
failures that never contain the token, and no dispatch over HTTP.

`tests/test_accept_anchors.py` pins the gate between the anchor workflow's two jobs (only valid,
published heads and genuine proofs get committed; upgrades only add), and `tests/test_workflows.py`
locks the workflows' security shape as text: SHA-pinned actions, the stamp/commit job split, the
hash-locked install, timeouts, optional heartbeats.

`tests/test_verify_site.py` covers the `verify.py --site` plumbing (live API +
static `index.json` fallback) with mocked HTTP — no network.

## Worked example: stats.js model calibration

`tests/test_stats_js.py` checks the model as well as its implementation:

1. Each draw samples `m` distinct numbers from `K`, so the frequency statistic is
   `Q = Σ(O−E)² / [E·(K−m)/(K−1)]`, where `E = n·m/K`. For large samples it follows
   approximately `χ²(K−1)`; theoretical expected counts are not a second sample.
2. Run the JS under Node against numerical baselines and an independent survival
   function using finite sums / `erfc`. Fixed-seed uniform simulations check the
   rejection rate; a biased sample checks that real deviations are detected.
3. With fewer than five expected inclusions or omissions per number, retain bars
   but omit the approximate p-value. Rendering tests cover this state and ensure
   that failing to reject uniformity is not presented as proof of fairness.

Statistical baselines can change when correcting the model. They are separate
from the frozen draw-algorithm and commitment vectors below.

## Locking the frozen algorithm

The draw algorithm and the commitment formula are **frozen** (see `SPEC.md`).
Their golden-vector tests are guardrails, not TODOs: if a change turns them red,
the change is wrong (or needs a new algorithm version), not the test.
