# Execution record

Local execution of `python -m pytest -v --tb=short tests/test_quality.py tests/test_warehouse.py`. The input and output shown come from the repository example or test fixtures.

- `python -m pytest -v --tb=short tests/test_quality.py tests/test_warehouse.py` — exit 0.

The image renders the captured terminal output. [Full transcript](screenshots/execution.txt).

Latest local test output:

```text
........................................................................ [ 91%]
.......                                                                  [100%]
=============================== warnings summary ===============================
../../python-env/lib/python3.12/site-packages/fastapi/testclient.py:1
  ./python-env/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
79 passed, 1 warning in 6.70s
```

This record covers the local commands and fixtures shown. External services and deployment remain unverified unless explicitly listed.
