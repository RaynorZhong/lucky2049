# Test-Driven Development guide

This project is set up for a fast red → green → refactor loop.

## The loop

1. **Red** — write a test for the behavior you want; run it; watch it fail for
   the right reason (assertion/404, not an import error).
2. **Green** — write the minimum code to make it pass.
3. **Refactor** — clean up with the test as a safety net; keep it green.

Keep tests running on every save:

```shell
make install-dev    # one-time: pytest + pytest-cov + pytest-watcher into the venv
make watch          # re-runs the suite on every file change (the TDD loop)
```

Other commands:

```shell
make test           # run once
make cov            # run with a coverage report (term-missing)
./.venv/bin/python -m pytest tests/test_healthz.py -v     # one file
./.venv/bin/python -m pytest -k commitment                # by keyword
```

## Where tests go

`tests/test_*.py`. Plain `unittest.TestCase` classes and pytest-style functions
both run under pytest, so use whichever fits.

## Writing isolated tests (no real database)

`tests/conftest.py` points the DB at a throwaway file and disables DB logging
*before anything imports the app*, so a test can never touch the real 170MB
`database.db`. Two fixtures:

- **`db`** — pristine, empty tables for this test (drop + create). Use it for
  logic that reads/writes the database.
- **`client`** — a FastAPI `TestClient` backed by `db`, with **no** lifespan
  (no `init_db`, no scheduler, no CSV seeding). Use it for endpoint tests.

```python
def test_something_with_db(db):
    from app.models import create_draw, get_draw_by_id
    create_draw([(0, [1,2,3,4,5], [1,2], "t", 0, 143)])
    assert get_draw_by_id(0).front_list == [1, 2, 3, 4, 5]

def test_an_endpoint(client):
    assert client.get("/healthz").status_code == 200
```

## Worked example: `/healthz` (test-first)

`tests/test_healthz.py` was written **before** the route existed:

1. The test asserted `GET /healthz` returns 200 with `status: "ok"` and an
   empty-DB snapshot (`draws == 0`, genesis commitment head). Running it gave
   `404` / `KeyError` — **red**.
2. A four-line route in `app/main.py` (built on the existing `get_commitment_head()`)
   made it **green**.

Use it as the template for the next feature.

## Locking the frozen algorithm

The draw algorithm and the commitment formula are **frozen** (see `SPEC.md`).
Their golden-vector tests (`tests/test_spec_v1.py`, `tests/test_commitment.py`)
are guardrails, not TODOs: if a change turns them red, the change is wrong (or
needs a new algorithm version), not the test.
