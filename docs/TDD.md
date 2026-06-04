# Test-Driven Development guide

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

Everything is **stdlib + Node only** — no database, no fixtures, no heavy deps.
The site is static, so the tests pin the two things that matter:

- **The frozen algorithm + commitment** — golden vectors in `tests/test_spec_v1.py`
  and `tests/test_commitment.py` recompute against `verify.py` and SPEC.md.
- **The in-browser JS** — `tests/test_verify_js.py` and `tests/test_stats_js.py`
  run `static/verify.js` / `static/stats.js` under Node and check they reproduce
  the Python result (and frozen golden values). They self-skip if Node is absent.

`tests/test_verify_site.py` covers the `verify.py --site` plumbing (live API +
static `index.json` fallback) with mocked HTTP — no network.

## Worked example: stats.js parity (golden-pinned)

`tests/test_stats_js.py` is the template for a cross-implementation lock:

1. Compute the authoritative answer once (here, the chi-square via scipy during
   development) and **hard-code it as a golden vector** in the test.
2. Run the JS (`static/stats.js`) under Node over a deterministic dataset and
   assert it matches the golden — and so does an independent pure-Python
   reference of the same formula. No scipy dependency is left in the repo.

Same discipline as the frozen algorithm: pin the known-correct output, then let
any drift in either implementation turn it red.

## Locking the frozen algorithm

The draw algorithm and the commitment formula are **frozen** (see `SPEC.md`).
Their golden-vector tests are guardrails, not TODOs: if a change turns them red,
the change is wrong (or needs a new algorithm version), not the test.
