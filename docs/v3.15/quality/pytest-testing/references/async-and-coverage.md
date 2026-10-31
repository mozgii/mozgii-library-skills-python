---
title: Async tests, coverage and parallel pytest runs
author: MOZGII, Inc.
lang: en
---

# Async tests, coverage and parallel pytest runs

Running asyncio code under pytest with pytest-asyncio or anyio, measuring line and branch coverage with coverage.py, and running the suite in parallel and in random order to prove the tests are independent.

## Contents

This reference has 9 sections.

1. pytest-asyncio modes and loop scopes
2. Async fixtures
3. anyio and trio instead of pytest-asyncio
4. Coverage configuration
5. Coverage in CI and across processes
6. Parallel runs with pytest-xdist
7. Random order with pytest-randomly
8. Snapshot tests with syrupy
9. Gotchas

## 1. pytest-asyncio modes and loop scopes

pytest-asyncio 1.x runs `async def` tests on an asyncio event loop; the mode decides which tests it picks up.

```toml
[tool.pytest]
asyncio_mode = "auto"                         # every async test and fixture; "strict" needs markers
asyncio_default_fixture_loop_scope = "function"
asyncio_default_test_loop_scope = "function"
asyncio_debug = true                          # 1.2+: asyncio debug mode (slow callbacks, unawaited coroutines)
```

```python
import pytest


async def test_one_loop_per_test() -> None:            # auto mode: no marker needed
    assert await compute() == 42


@pytest.mark.asyncio(loop_scope="module")              # share one loop across this module's tests
async def test_shares_module_loop() -> None: ...
```

- The `event_loop` fixture was removed in 1.0; use `loop_scope` on markers and fixtures instead.
- A fixture and the tests that use it must run on the same loop: a `module`-scoped async fixture needs `loop_scope="module"` on the fixture and on the tests, or the tests fail with "attached to a different loop".
- Custom loops (uvloop, a selector loop on Windows) come from the `pytest_asyncio_loop_factories` hook (1.4) instead of overriding `event_loop_policy`, which is deprecated.

## 2. Async fixtures

An async fixture is an `async def` (or async generator) fixture; in auto mode it needs no special decorator.

```python
from collections.abc import AsyncIterator

import pytest


@pytest.fixture
async def client() -> AsyncIterator[ApiClient]:
    async with ApiClient("http://127.0.0.1:8080") as c:     # teardown runs on the same loop
        yield c


async def test_health(client: ApiClient) -> None:
    assert (await client.get("/health")).status == 200
```

In strict mode, decorate async fixtures with `@pytest_asyncio.fixture`.

## 3. anyio and trio instead of pytest-asyncio

The anyio pytest plugin ships with anyio and runs tests on asyncio or trio; use it when the code is written against anyio.

```python
import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"                               # or "trio"; parametrize to run both


async def test_with_anyio() -> None:
    assert await compute() == 42
```

Do not enable pytest-asyncio's auto mode and anyio markers on the same tests; pick one runner per project.

## 4. Coverage configuration

coverage.py measures which lines and branches the tests executed; pytest-cov runs it around pytest.

```toml
[tool.coverage.run]
branch = true                     # count both outcomes of every if/while
source_pkgs = ["my_app"]          # the installed package, by import name
parallel = true                   # one data file per process; combined afterwards

[tool.coverage.report]
show_missing = true
skip_covered = true
fail_under = 85
exclude_also = [
  "if TYPE_CHECKING:",
  "raise NotImplementedError",
  "@overload",
  "if __name__ == .__main__.:",
]
```

```bash
uv run pytest --cov --cov-report=term-missing            # settings come from [tool.coverage]
uv run pytest --cov --cov-report=html && open htmlcov/index.html
uv run coverage report --fail-under=85
```

- Branch coverage finds untested `else` paths that line coverage reports as covered.
- On Python 3.12+ coverage.py can measure with `sys.monitoring`, which is much cheaper; set `core = "sysmon"` under `[tool.coverage.run]` (or `COVERAGE_CORE=sysmon`) if your version does not choose it already.
- Coverage shows what ran, not what was checked: a test without asserts covers lines too. Use it to find untested code, not as a quality score.

## 5. Coverage in CI and across processes

Code run in subprocesses or in xdist workers is measured only when coverage starts in each process.

- pytest-cov handles xdist workers automatically.
- For code you start with `subprocess`, set `[tool.coverage.run] patch = ["subprocess"]` (coverage 7.10+), then `uv run coverage combine` before reporting.
- Upload `coverage.xml` (`--cov-report=xml`) to the CI's coverage view; keep the `fail_under` floor in the repo, not only in the CI settings.

## 6. Parallel runs with pytest-xdist

pytest-xdist runs tests in several worker processes, which shortens long suites and exposes hidden dependencies between tests.

```bash
uv run pytest -n auto                     # one worker per CPU
uv run pytest -n 4 --dist loadscope       # keep each module's (or class's) tests on one worker
uv run pytest -n auto --dist loadgroup    # tests marked @pytest.mark.xdist_group("db") share a worker
```

- Session fixtures run once per worker, not once per run. A fixture that creates a database must use a unique name per worker: the `worker_id` fixture gives `gw0`, `gw1`, ... (or `master` without xdist).
- `-n auto` adds start-up time; for suites under a few seconds it is slower than a single process.

## 7. Random order with pytest-randomly

pytest-randomly shuffles test order and reseeds `random` (and numpy, Faker) for every test, so order-dependent tests fail where you can see them.

```bash
uv add --dev pytest-randomly
uv run pytest                              # random order; the seed is printed in the header
uv run pytest --randomly-seed=1234          # replay a failing order (or --randomly-seed=last)
uv run pytest -p no:randomly               # turn it off for one run
```

## 8. Snapshot tests with syrupy

A snapshot test compares output with a stored copy, which suits large structured results (rendered text, JSON responses) that are tedious to write as asserts.

```python
from syrupy.assertion import SnapshotAssertion


def test_report_rendering(snapshot: SnapshotAssertion) -> None:
    assert render_report(sample_data()) == snapshot
```

```bash
uv run pytest --snapshot-update            # write or refresh snapshots in __snapshots__/
```

Review snapshot diffs in code review like code; an unreviewed `--snapshot-update` accepts bugs.

## 9. Gotchas

These are the async and coverage problems seen most often.

1. **`RuntimeError: ... attached to a different loop`.** A fixture created an object on one loop and a test used it on another. Give the fixture and its tests the same `loop_scope`.
2. **Coverage reports 0% for the package.** `source` names a folder path while tests import the installed package, or the package was imported before coverage started (a pytest plugin importing it). Use the import name in `source`, and run through `pytest --cov`.
3. **Coverage misses code in subprocesses.** Only the main process was measured. Enable `patch = ["subprocess"]` and `coverage combine`.
4. **xdist workers fight over one database or file.** Session fixtures run per worker. Suffix resources with `worker_id`.
5. **Snapshot tests always pass.** Someone ran `--snapshot-update` in CI. Never pass it in CI; fail on missing snapshots instead.
