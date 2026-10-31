---
name: pytest-testing
description: Writes and runs Python tests with pytest 9 - native [tool.pytest] TOML configuration and strict mode, test layout with src/, plain asserts, pytest.raises with match, parametrize with ids, fixtures with scopes, yield teardown and factories, tmp_path, monkeypatch, capsys and caplog, markers, subtests, mocking with fakes, monkeypatch, unittest.mock and autospec, async tests with pytest-asyncio or anyio, coverage with branch measurement, parallel runs with xdist, and property-based tests with Hypothesis. Use when adding tests to a Python project, setting up pytest, fixing flaky or slow tests, measuring coverage, or testing async code.
metadata:
  tags:
  - python
  - pytest
  - testing
  - fixtures
  - mocking
  - coverage
  - hypothesis
  - pytest-asyncio
  - xdist
---

# Testing Python with pytest

pytest finds functions named `test_*` in files named `test_*.py`, runs them, and reports each failing `assert` with the values on both sides. Fixtures supply what a test needs and clean it up afterwards. This skill sets pytest up, writes good tests, and keeps the suite fast and trustworthy; details live in the reference files.

## Step 0: read the installed versions

pytest 9 changed configuration and strictness defaults, and plugins follow it, so read the versions first.

```bash
uv run pytest --version                     # e.g. pytest 9.2.0
uv pip list | grep -iE "pytest|hypothesis|coverage"
grep -nA12 "tool.pytest" pyproject.toml
```

Current releases (October 2026): pytest 9.2, pytest-asyncio 1.4, pytest-cov 7.1, coverage 7.17, pytest-xdist 3.8, pytest-mock 3.17, Hypothesis 6.170, syrupy 6.1.

## Install and lay out

Test tools go in the dev dependency group; tests live beside `src/`, not inside the package.

```bash
uv add --dev pytest pytest-cov pytest-xdist hypothesis
uv add --dev pytest-asyncio                  # only for asyncio code
```

```
my-app/
  pyproject.toml
  src/my_app/prices.py
  tests/
    conftest.py              # fixtures shared by every test below this folder
    test_prices.py
    integration/
      conftest.py            # fixtures only integration tests need
      test_api.py
```

With the `src/` layout, tests import the installed package (`uv sync` installs it editable), so a missing file in the package fails in tests too. Use `--import-mode=importlib` so test files with the same name in different folders do not clash and need no `__init__.py`.

## Configuration in pyproject.toml

pytest 9 reads a native TOML table `[tool.pytest]` whose values keep their TOML types. The older `[tool.pytest.ini_options]` still works, but a file may not have both.

```toml
[tool.pytest]
minversion = "9.0"
testpaths = ["tests"]
addopts = ["-ra", "--import-mode=importlib"]
strict = true                               # strict_config, strict_markers, strict_xfail, strict_parametrization_ids
filterwarnings = ["error"]                  # a warning in a test is a failure
markers = [
  "slow: takes more than a second",
  "integration: needs a database or network",
]
asyncio_mode = "auto"                       # pytest-asyncio: every async def test runs on a loop
asyncio_default_fixture_loop_scope = "function"
```

- `strict = true` turns typos into errors: an unregistered marker, an unknown config key, an `xfail` test that passes, duplicate parametrize ids.
- `filterwarnings = ["error"]` makes deprecations visible early; allow a specific one with `"ignore::DeprecationWarning:some_lib.*"`.
- `-ra` prints a summary line for every skip, xfail and error.

## Choose the right test tool

Pick the tool from what varies and what must be replaced; each row names why the nearest alternative loses.

| You need | Use | Why the others lose here |
|---|---|---|
| one check over inputs known in advance | `@pytest.mark.parametrize` | a loop stops at the first failure and reports one test |
| cases that exist only while the test runs | the `subtests` fixture ([fixtures-and-mocking.md](references/fixtures-and-mocking.md)) | `parametrize` needs them at collection time |
| a rule that must hold for any input | Hypothesis `@given` | hand-picked rows miss the edge cases |
| a resource built and cleaned up for tests | a fixture with `yield` | setup inside a test leaks when an assert fails |
| a slow or outside collaborator you can pass in | a small fake | a mock checks the calls you predicted, not the behaviour |
| a collaborator you cannot pass in | `patch(..., autospec=True)` | a bare `patch` accepts calls the real function would refuse |
| an env var, a cwd or an attribute for one test | `monkeypatch` | manual set and restore leaks on failure |
| a large output that changes rarely | a snapshot (syrupy) | hand-written expected text goes stale unread |

## Writing a test

A good test arranges inputs, calls one thing, and asserts on the result with a plain `assert`.

```python
# src/my_app/prices.py
from decimal import Decimal


def parse_price(raw: str) -> Decimal:
    price = Decimal(raw.strip().removeprefix("$"))     # raises InvalidOperation on garbage
    if price < 0:
        raise ValueError(f"negative price: {raw!r}")
    return price
```

```python
# tests/test_prices.py
from decimal import Decimal

import pytest

from my_app.prices import parse_price


def test_parse_price_strips_currency_sign() -> None:
    assert parse_price("$3.50") == Decimal("3.50")


def test_parse_price_rejects_negative() -> None:
    with pytest.raises(ValueError, match="negative price"):
        parse_price("-1")


def test_float_sum() -> None:
    assert 0.1 + 0.2 == pytest.approx(0.3)
```

- Name tests for the behaviour: `test_parse_price_rejects_negative`, not `test_parse_price_2`.
- Always pass `match=` to `pytest.raises`; without it, any `ValueError` from anywhere passes the test.
- `pytest.approx` compares floats, and since 9.1 also `datetime` and `timedelta` with an explicit `abs` tolerance.

## Parametrize: one test, many cases

`@pytest.mark.parametrize` runs the same test body for each row of inputs, with a readable id per case.

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3.50", Decimal("3.50")),
        ("  7 ", Decimal("7")),
        pytest.param("1e2", Decimal("100"), id="scientific"),
        pytest.param("abc", None, marks=pytest.mark.xfail(raises=ArithmeticError, strict=True), id="garbage"),
    ],
)
def test_parse_price_accepts_common_forms(raw: str, expected: Decimal) -> None:
    assert parse_price(raw) == expected
```

Since pytest 9.2, `ids`, `indirect` and `scope` are keyword-only. Run one case with `pytest "tests/test_prices.py::test_parse_price_accepts_common_forms[scientific]"`.

## Fixtures

A fixture is a function that builds something a test needs; a test asks for it by naming it as a parameter.

```python
# tests/conftest.py
import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest


@pytest.fixture
def prices_file(tmp_path: Path) -> Path:            # tmp_path: a fresh directory per test
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"A1": "$3.50"}), encoding="utf-8")
    return path


@pytest.fixture
def make_prices(tmp_path: Path) -> Callable[[dict[str, str]], Path]:   # a factory: the test chooses the data
    def make(data: dict[str, str]) -> Path:
        path = tmp_path / f"prices-{len(data)}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
    return make


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:   # yield: code after it is the teardown
    path = tmp_path_factory.mktemp("db") / "scratch.sqlite3"
    sqlite3.connect(path).close()
    yield f"sqlite:///{path}"
    path.unlink()
```

- Scopes: `function` (default), `class`, `module`, `package`, `session`. A wider scope is faster and shares state, so only immutable or reset-between-tests resources get one.
- Use factories instead of many near-identical fixtures.
- `conftest.py` fixtures are visible to every test at or below its folder; never import from `conftest.py`.
- `uv run pytest --fixtures` lists all fixtures; `--setup-show` prints setup and teardown order.

Built-in fixtures cover most needs: `tmp_path`, `monkeypatch` (env vars, attributes, `chdir`), `capsys`/`capfd` (printed output), `caplog` (log records), `recwarn`. More in [fixtures-and-mocking.md](references/fixtures-and-mocking.md).

## Markers, skips and expected failures

Markers label tests so you can select, skip or expect them to fail.

```python
import sys

import pytest


@pytest.mark.slow
def test_full_import() -> None: ...


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX signals")
def test_sigterm_handling() -> None: ...


@pytest.mark.xfail(reason="bug #412: rounding", strict=True)
def test_rounding_half_even() -> None: ...
```

Select with `-m`: `uv run pytest -m "not slow and not integration"`. With `strict = true`, an `xfail` that starts passing fails the run, so fixed bugs get their marker removed.

## Running tests

The command-line options below cover daily work, debugging and CI.

```bash
uv run pytest -q                           # quiet
uv run pytest -x --lf                      # stop at first failure; rerun only last failures
uv run pytest -k "price and not negative"  # select by name expression
uv run pytest tests/test_prices.py::test_parse_price_rejects_negative -vv
uv run pytest --pdb                        # debugger at the failing line
uv run pytest --durations=10               # slowest tests
uv run pytest -n auto                      # pytest-xdist: one worker per CPU
uv run pytest --cov --cov-branch --cov-report=term-missing --cov-fail-under=85
```

Exit codes: 0 all passed, 1 some failed, 2 interrupted, 3 internal error, 4 usage error (also for a missing plugin since 9.2), 5 no tests collected.

## Mocking: fakes first

Replace a collaborator only at the boundary of your code (network, clock, filesystem outside `tmp_path`, randomness), and prefer a small fake over a mock.

```python
from my_app import pricing


class FakeRates:
    def __init__(self, rates: dict[str, float]) -> None:
        self.rates = rates

    def rate(self, currency: str) -> float:
        return self.rates[currency]


def test_convert_uses_rate() -> None:
    assert pricing.convert(10.0, "EUR", rates=FakeRates({"EUR": 0.5})) == 5.0
```

When a dependency is not injectable, patch where it is looked up, with `autospec` so the fake keeps the real signature:

```python
# src/my_app/client.py
import httpx


def fetch(url: str) -> httpx.Response:
    try:
        return httpx.get(url, timeout=5.0)
    except httpx.TimeoutException:
        return httpx.get(url, timeout=5.0)                           # one retry
```

```python
# tests/test_client.py
from unittest.mock import patch

import httpx

from my_app import client


def test_fetch_retries_once() -> None:
    with patch("my_app.client.httpx.get", autospec=True) as get:     # my_app.client imported httpx
        get.side_effect = [httpx.TimeoutException("slow"), httpx.Response(200)]
        assert client.fetch("https://example.com").status_code == 200
    assert get.call_count == 2
```

Patterns, `pytest-mock`, time and randomness: [fixtures-and-mocking.md](references/fixtures-and-mocking.md).

## Async tests

With pytest-asyncio in auto mode, an `async def test_*` runs on a fresh event loop and async fixtures work the same way.

```python
import asyncio


async def test_gather_keeps_the_input_order() -> None:
    async def shout(name: str) -> str:
        await asyncio.sleep(0)
        return name.upper()

    assert await asyncio.gather(shout("a"), shout("b")) == ["A", "B"]
```

In strict mode (the pytest-asyncio default) an unmarked async test fails with "async def functions are not natively supported"; set `asyncio_mode = "auto"` or mark each test `@pytest.mark.asyncio`. Loop scopes, anyio and trio: [async-and-coverage.md](references/async-and-coverage.md).

## Property-based tests

Hypothesis generates inputs from a description of valid data and shrinks a failure to the smallest example, which finds edge cases that hand-picked examples miss.

```python
from decimal import Decimal

from hypothesis import given, strategies as st

from my_app.prices import parse_price


@given(st.decimals(min_value=0, max_value=10**6, places=2, allow_nan=False, allow_infinity=False))
def test_parse_price_round_trips(value: Decimal) -> None:
    assert parse_price(str(value)) == value
```

Strategies, round-trip and oracle properties, stateful tests and CI settings: [property-based-testing.md](references/property-based-testing.md).

## Verify

Prove the suite collects, passes in random order and in parallel, and that a broken test really fails.

```bash
uv run pytest --collect-only -q | tail -1           # the count you expect; exit code 5 means none were found
uv run pytest -q; echo "exit $?"                    # expect 0
uv run pytest -q -p randomly -n auto                # needs pytest-randomly and pytest-xdist: finds shared state
printf 'def test_probe() -> None:\n    assert 1 == 2\n' > tests/test_probe.py
uv run pytest -q tests/test_probe.py; echo "exit $?"   # expect 1
rm tests/test_probe.py
grep -rn "pytest.raises(" tests | grep -v "match="  # expect nothing
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `pytest.raises(ValueError)` alone | `match=`, so only the expected failure passes |
| `assert result` | assert the value you expect |
| mocking your own code | a fake at the boundary, or real objects |
| `time.sleep` to wait for something | inject the clock, or poll with a deadline |
| tests that share a module-level list | a fixture per test, or `monkeypatch` |
| importing from `conftest.py` | a fixture, or a helper module |
| a test that needs another test to run first | each test builds its own state |
| `xfail` without a reason | `reason=` and `strict=True` |

## Checklist

A test suite is in good shape when every item holds.

- [ ] `[tool.pytest]` with `strict = true`, `filterwarnings = ["error"]` and registered markers.
- [ ] Tests import the installed package (`src/` layout), not the source folder.
- [ ] Each test checks one behaviour, named for it; `pytest.raises` always has `match=`.
- [ ] Mocks only at boundaries, with `autospec`; fakes preferred.
- [ ] No test depends on another test's order or leftovers (run with `-p randomly` or `-n auto` to prove it).
- [ ] Coverage measured with branches; the floor (`--cov-fail-under`) is checked in CI.
- [ ] The full suite runs in CI on the lowest and highest supported Python.

## Gotchas

These are the failures that waste the most time in pytest suites.

1. **`async def functions are not natively supported`.** No async plugin handles the test: pytest-asyncio is missing, or it runs in strict mode and the test has no `@pytest.mark.asyncio`. Install it and set `asyncio_mode = "auto"`. (Before pytest 8.4 such tests were skipped with only a warning.)
2. **`ModuleNotFoundError: my_app` in tests.** The package is not installed in the test environment. Run through `uv run`, which syncs the project, or give the project a build system.
3. **A patch has no effect.** You patched where the name is defined, not where it is looked up. Patch the name in the module that uses it: `my_app.client.httpx.get` when `my_app.client` does `import httpx`, or `my_app.client.get` when it does `from httpx import get`.
4. **Tests pass alone and fail together.** Shared state: a module-level cache, an env var, a session fixture mutated by one test. Use `monkeypatch`, reset caches in a fixture, run with random order to find it.
5. **The suite slows down over time.** `uv run pytest --durations=20` finds the worst; widen fixture scope for expensive immutable setup, mark real slow tests and run them in a separate CI job.
6. **`pytest x.py[a]` is now a usage error.** pytest 9 refuses it; use the full node id `x.py::test_name[a]`.
7. **Duplicate parametrize ids fail under `strict`.** Give explicit `id=` values; they also make failures readable.

## See also

- `coverage-py`: branch coverage and combining data in depth
- `python-asyncio`: the code under async tests
- `nox-sessions`: running the suite on every Python
- `python-code-design`: code that needs few mocks
- `python-data-models`: fixtures built from typed models
