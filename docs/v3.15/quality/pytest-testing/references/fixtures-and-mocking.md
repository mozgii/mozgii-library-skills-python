---
title: pytest fixtures and mocking
author: MOZGII, Inc.
lang: en
---

# pytest fixtures and mocking

How to build test inputs with fixtures, isolate tests from the environment, and replace collaborators with fakes or mocks without making the tests brittle.

## Contents

This reference has 8 sections.

1. Fixture scopes and teardown
2. Fixture factories and parametrized fixtures
3. Built-in fixtures: tmp_path, monkeypatch, capsys, caplog
4. Fakes versus mocks
5. unittest.mock: patch, autospec, side_effect
6. pytest-mock's mocker fixture
7. Time, randomness and the network
8. Gotchas

## 1. Fixture scopes and teardown

A fixture's scope says how long one instance lives; code after `yield` is the teardown and runs even when the test fails.

```python
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def scratch_db() -> Iterator[str]:
    url = create_database("test_my_app")       # expensive: once per run
    yield url
    drop_database(url)


@pytest.fixture
def db(scratch_db: str) -> Iterator[Connection]:
    conn = connect(scratch_db)
    tx = conn.begin()                          # each test runs in a transaction ...
    yield conn
    tx.rollback()                              # ... rolled back, so tests never see each other's rows
    conn.close()
```

- A fixture may use fixtures of the same or wider scope, never narrower: a `session` fixture cannot ask for `tmp_path` (use `tmp_path_factory`).
- `autouse=True` applies a fixture to every test in its scope without naming it; keep autouse fixtures few and obvious (resetting a cache, blocking the network).
- Since pytest 9.1, class-scoped fixtures written as plain instance methods are deprecated, and 9.2 extends that to `module`, `package` and `session` scope: use a module-level fixture or `@classmethod`.

## 2. Fixture factories and parametrized fixtures

A factory fixture returns a function, so each test builds exactly the data it needs; a parametrized fixture runs every test that uses it once per parameter.

```python
from collections.abc import Callable
from dataclasses import dataclass

import pytest


@dataclass
class User:
    name: str
    admin: bool = False


@pytest.fixture
def make_user() -> Callable[..., User]:
    created: list[User] = []

    def make(name: str = "ada", *, admin: bool = False) -> User:
        user = User(name, admin)
        created.append(user)
        return user

    return make


@pytest.fixture(params=["sqlite", "postgres"], ids=["sqlite", "pg"])
def backend(request: pytest.FixtureRequest) -> str:
    return request.param


def test_admin_can_delete(make_user: Callable[..., User], backend: str) -> None:
    assert make_user(admin=True).admin
```

## 3. Built-in fixtures: tmp_path, monkeypatch, capsys, caplog

pytest's built-in fixtures isolate a test from the filesystem, environment and output streams.

```python
import logging
import os
from pathlib import Path

import pytest


def test_writes_report(tmp_path: Path) -> None:
    out = tmp_path / "report.txt"              # unique per test, kept for the last 3 runs
    write_report(out)
    assert out.read_text(encoding="utf-8").startswith("Report")


def test_reads_config_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MY_APP_CONFIG", str(tmp_path / "c.toml"))
    monkeypatch.delenv("MY_APP_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("my_app.settings.DEFAULT_TIMEOUT", 0.1)
    assert load_settings().timeout == 0.1


def test_cli_prints_summary(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--summary"])
    out, err = capsys.readouterr()
    assert "3 items" in out and err == ""


def test_warns_on_retry(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="my_app"):
        fetch_with_retry()
    assert [r.message for r in caplog.records] == ["retrying after timeout"]
```

`monkeypatch` undoes every change after the test. `capfd` captures at the file-descriptor level, which also catches output from C extensions and subprocesses.

### The subtests fixture

The `subtests` fixture (pytest 9, experimental) reports each loop iteration separately and keeps going after a failure.

```python
import pytest

from my_app.prices import parse_price


def test_every_sample_parses(subtests: pytest.Subtests) -> None:
    for raw in ["1", "2.5", "$3"]:
        with subtests.test(raw=raw):
            assert parse_price(raw) > 0
```

Prefer `parametrize` when the cases are known at collection time; use subtests when they come from data read inside the test.

## 4. Fakes versus mocks

A fake is a small working implementation; a mock records calls and returns canned values. Fakes make tests survive refactoring; mocks tie tests to call details.

| Use | When |
|---|---|
| the real thing | pure functions, value objects, `tmp_path` files, an in-memory SQLite, a scratch Postgres |
| a fake class | a collaborator with a small interface you own (a repository, a clock, a mailer) |
| `monkeypatch.setattr` | one function or constant to replace, no call assertions needed |
| `unittest.mock` with `autospec` | a third-party API you cannot inject, or when the call itself is the behaviour (an email was sent once) |

Design for injection where you can: a function that takes `clock: Callable[[], datetime] = datetime.now` needs no patching at all.

## 5. unittest.mock: patch, autospec, side_effect

`unittest.mock.patch` replaces a name for the duration of a `with` block or a decorated test; patch the name where the code under test looks it up.

```python
from unittest.mock import AsyncMock, patch

# my_app/notify.py does: from my_app.mail import send
with patch("my_app.notify.send", autospec=True) as send:
    notify_user("ada@example.com")
    send.assert_called_once_with("ada@example.com", subject="Welcome")

with patch("my_app.client.fetch_json", autospec=True) as fetch:
    fetch.side_effect = [TimeoutError(), {"ok": True}]     # first call raises, second returns
    assert load_status() == {"ok": True}

with patch("my_app.client.fetch_async", new_callable=AsyncMock) as fetch:
    fetch.return_value = {"ok": True}                      # awaitable mock for async code
```

- `autospec=True` makes the mock refuse calls the real function would refuse (wrong arguments, misspelled methods). Without it, `mock.asert_called_once()` (typo) silently passes on old Pythons and any attribute exists.
- `patch.object(SomeClass, "method", autospec=True)` patches an attribute of an object you already hold.
- Assert on outcomes first (return values, state, files); assert on calls only when the call is the point.

## 6. pytest-mock's mocker fixture

pytest-mock wraps `unittest.mock` in a fixture, so patches end with the test without `with` blocks.

```python
from pytest_mock import MockerFixture


def test_sends_once(mocker: MockerFixture) -> None:
    send = mocker.patch("my_app.notify.send", autospec=True)
    notify_user("ada@example.com")
    send.assert_called_once()


def test_spy_keeps_real_behaviour(mocker: MockerFixture) -> None:
    spy = mocker.spy(my_app.cache, "compute")
    my_app.cache.get("k"); my_app.cache.get("k")
    assert spy.call_count == 1                              # the second call hit the cache
```

## 7. Time, randomness and the network

Time, random numbers and network access make tests flaky; control each one explicitly.

- **Time:** inject a clock, or use `time-machine` (`@time_machine.travel("2026-01-01 00:00Z", tick=False)`) to freeze `datetime.now()` and `time.time()`. Never `time.sleep` in a test to wait for something; poll with a timeout or await an event.
- **Randomness:** seed per test (`random.Random(1234)` passed in), or let `pytest-randomly` reseed `random`, numpy and Faker per test and print the seed for reproduction.
- **Network:** block it by default with `pytest-socket` (`--disable-socket`) or an autouse fixture, and serve HTTP from a local fake (`respx` for httpx, `responses` for requests, or a real local server on port 0).

## 8. Gotchas

These are the fixture and mock mistakes that make suites lie.

1. **A mock passes a test that should fail.** No `autospec`, so a renamed function or a wrong argument still "works". Always use `autospec=True` or `create_autospec`.
2. **`ScopeMismatch: You tried to access the function scoped fixture tmp_path with a session scoped request`.** A wide-scope fixture asked for a narrow one. Use `tmp_path_factory.mktemp("data")`.
3. **Teardown never ran.** The fixture used `return` after acquiring a resource, or the code before `yield` raised halfway. Put each resource in its own fixture so a failure in one does not leak the other.
4. **Leaked environment between tests.** `os.environ[...] = ...` in a test. Use `monkeypatch.setenv`, which restores the old value.
5. **Too many mocks.** A test that patches five things tests the mocks. Move logic into pure functions and test those without mocks; keep one integration test for the wiring.
